from enum import StrEnum

from llm_tool_cli.paths import ProjectPathId, ProjectRootPath
from llm_tool_cli.protocol.logic_cells.base import LogicCell
from llm_tool_cli.protocol.logic_cells.uniform import UniformCell
from llm_tool_cli.protocol.output_cells.base import MetaValue, OutputCell

from vcsinstall.domain import Dependency, DependencyName, SourcePath, SourceRevision
from vcsinstall.runtime.entities import DependencyPlan, LocalStatus, SyncPlan
from vcsinstall.sources import ResolvedSource
from vcsinstall.workspace import Entry


class Outcome(StrEnum):
    added = "added"
    replaced = "replaced"
    updated = "updated"
    unchanged = "unchanged"
    installed = "installed"
    removed = "removed"
    synced = "synced"


class RefKind(StrEnum):
    default = "default"
    branch = "branch"
    tag = "tag"
    revision = "revision"


class Operation(StrEnum):
    dependency_add = "dependency add"
    dependency_list = "dependency list"
    dependency_outdated = "dependency outdated"
    dependency_remove = "dependency remove"
    dependency_update = "dependency update"
    dependency_sync_to = "dependency sync-to"
    add = "add"
    remove = "remove"
    install = "install"
    list = "list"


class RemovalCell(UniformCell):
    dependency: DependencyName
    destination: ProjectPathId
    keep_files: bool

    def _render(self, cell_type: type[OutputCell]) -> OutputCell:
        return cell_type.build_meta(
            "removal",
            type="removal",
            operation=Operation.remove,
            dependency=self.dependency,
            destination=self.destination,
            outcome=Outcome.removed,
            keep_files=self.keep_files,
        )


class DependencyCell(UniformCell):
    operation: Operation = Operation.dependency_add
    dependency: DependencyName
    definition: Dependency
    revision: SourceRevision | None
    outcome: Outcome | None = None
    previous_revision: SourceRevision | None = None
    redefinition: bool = False

    def _render(self, cell_type: type[OutputCell]) -> OutputCell:
        definition = self.definition
        selector = definition.selector
        meta: dict[str, MetaValue] = dict(
            operation=self.operation,
            dependency=self.dependency,
            vcs=definition.vcs,
            repository=definition.repository,
            ref_kind=RefKind(selector.kind.value) if selector is not None else RefKind.default,
            ref=selector.value if selector is not None else None,
            revision=self.revision,
        )
        if self.outcome is not None:
            meta["outcome"] = self.outcome
        if self.redefinition:
            meta["previous_revision"] = self.previous_revision
        return cell_type.build_meta("dependency", type="dependency", **meta)


class DependencyListCell(DependencyCell):
    operation: Operation = Operation.dependency_list
    entries: int
    modified: int
    missing: int
    unlocked: int

    def _render(self, cell_type: type[OutputCell]) -> OutputCell:
        dependency = super()._render(cell_type)
        return cell_type.build_meta(
            "dependency",
            **dependency.meta,
            entries=self.entries,
            modified=self.modified,
            missing=self.missing,
            unlocked=self.unlocked,
        )


class DependencyOutdatedCell(DependencyCell):
    operation: Operation = Operation.dependency_outdated
    revision: SourceRevision
    current_revision: SourceRevision

    def _render(self, cell_type: type[OutputCell]) -> OutputCell:
        dependency = super()._render(cell_type)
        return cell_type.build_meta("dependency", **dependency.meta, current_revision=self.current_revision)


class InstallationCell(DependencyCell):
    destination: ProjectPathId
    source_path: SourcePath
    status: LocalStatus = LocalStatus.unmodified

    def _render(self, cell_type: type[OutputCell]) -> OutputCell:
        dependency = super()._render(cell_type)
        meta = dependency.meta | dict(
            type="installation",
            destination=self.destination,
            source_path=self.source_path,
            status=self.status,
        )
        if self.redefinition:
            meta["previous_revision"] = self.previous_revision
        return cell_type.build_meta(
            "installation",
            **meta,
        )


class SyncCell(InstallationCell):
    operation: Operation = Operation.dependency_sync_to
    source_dir: ProjectRootPath

    def _render(self, cell_type: type[OutputCell]) -> OutputCell:
        installation = super()._render(cell_type)
        return cell_type.build_meta("installation", **installation.meta, source_dir=str(self.source_dir))


def sync_cells(plan: SyncPlan) -> list[LogicCell]:
    """Report recipient effects while retaining the local installation baseline and status."""
    return [
        SyncCell(
            dependency=plan.name,
            definition=plan.definition,
            revision=plan.revision,
            destination=entry.destination,
            source_path=entry.source_path,
            status=entry.status,
            source_dir=plan.source_dir,
            outcome=Outcome.synced if entry.changed else Outcome.unchanged,
        )
        for entry in plan.entries
    ]


def entry_add_cell(entry: Entry, resolved: ResolvedSource, *, unchanged: bool) -> LogicCell:
    return InstallationCell(
        operation=Operation.add,
        dependency=entry.dependency,
        definition=resolved.dependency,
        revision=resolved.revision,
        outcome=Outcome.unchanged if unchanged else Outcome.installed,
        destination=entry.destination,
        source_path=entry.source_path,
    )


def _dependency_installation_cells(plan: DependencyPlan, *, operation: Operation) -> list[InstallationCell]:
    """Project the shared revision and entry changes for dependency mutation results."""
    installing = operation == Operation.install
    revision_unchanged = plan.lock_unchanged if installing else plan.previous_revision == plan.lock.revision
    return [
        InstallationCell(
            operation=operation,
            dependency=plan.name,
            definition=plan.definition,
            revision=plan.lock.revision,
            outcome=(
                Outcome.unchanged
                if not entry_plan.changed and revision_unchanged
                else Outcome.installed if installing else Outcome.updated
            ),
            previous_revision=plan.previous_revision,
            redefinition=not installing,
            destination=entry_plan.entry.destination,
            source_path=entry_plan.entry.source_path,
        )
        for entry_plan in plan.entries
    ]


def dependency_add_cells(plan: DependencyPlan, *, redefinition: bool, unchanged: bool) -> list[LogicCell]:
    """Build ordered dependency and installation results from the prepared plan and declaration outcome."""
    outcome = Outcome.unchanged if unchanged else Outcome.replaced if redefinition else Outcome.added
    cells: list[LogicCell] = [
        DependencyCell(
            dependency=plan.name,
            definition=plan.definition,
            revision=plan.lock.revision,
            outcome=outcome,
            previous_revision=plan.previous_revision,
            redefinition=redefinition,
        )
    ]
    cells.extend(_dependency_installation_cells(plan, operation=Operation.dependency_add))
    return cells


def dependency_change_cells(plans: list[DependencyPlan], *, operation: Operation) -> list[LogicCell]:
    """Report dependencies by name, followed by entries across the selection by destination."""
    installing = operation == Operation.install
    cells: list[LogicCell] = [
        DependencyCell(
            operation=operation,
            dependency=plan.name,
            definition=plan.definition,
            revision=plan.lock.revision,
            outcome=Outcome.unchanged if plan.unchanged else Outcome.installed if installing else Outcome.updated,
            previous_revision=plan.previous_revision,
            redefinition=not installing,
        )
        for plan in plans
    ]
    installations = [cell for plan in plans for cell in _dependency_installation_cells(plan, operation=operation)]
    installations.sort(key=lambda cell: cell.destination)
    cells.extend(installations)
    return cells
