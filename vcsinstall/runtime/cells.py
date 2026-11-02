from enum import StrEnum

from llm_tool_cli.paths import ProjectPathId
from llm_tool_cli.protocol.logic_cells.base import LogicCell
from llm_tool_cli.protocol.logic_cells.uniform import UniformCell
from llm_tool_cli.protocol.output_cells.base import OutputCell

from vcsinstall.domain import Dependency, DependencyName, SourcePath, SourceRevision
from vcsinstall.runtime.entities import EntryPlan
from vcsinstall.sources import ResolvedSource
from vcsinstall.workspace import Entry


class Outcome(StrEnum):
    added = "added"
    replaced = "replaced"
    updated = "updated"
    unchanged = "unchanged"
    installed = "installed"
    removed = "removed"


class RefKind(StrEnum):
    default = "default"
    branch = "branch"
    tag = "tag"
    revision = "revision"


class Operation(StrEnum):
    dependency_add = "dependency add"
    dependency_remove = "dependency remove"
    add = "add"
    remove = "remove"


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
    outcome: Outcome
    previous_revision: SourceRevision | None = None
    redefinition: bool = False

    def _render(self, cell_type: type[OutputCell]) -> OutputCell:
        definition = self.definition
        selector = definition.selector
        meta = dict(
            operation=self.operation,
            dependency=self.dependency,
            vcs=definition.vcs,
            repository=definition.repository,
            ref_kind=RefKind(selector.kind.value) if selector is not None else RefKind.default,
            ref=selector.value if selector is not None else None,
            revision=self.revision,
            outcome=self.outcome,
        )
        if self.redefinition:
            meta["previous_revision"] = self.previous_revision
        return cell_type.build_meta("dependency", type="dependency", **meta)


class InstallationCell(DependencyCell):
    destination: ProjectPathId
    source_path: SourcePath

    def _render(self, cell_type: type[OutputCell]) -> OutputCell:
        dependency = super()._render(cell_type)
        meta = dependency.meta | dict(
            type="installation",
            destination=self.destination,
            source_path=self.source_path,
            status="clean",
        )
        if self.redefinition:
            meta["previous_revision"] = self.previous_revision
        return cell_type.build_meta(
            "installation",
            **meta,
        )


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


def dependency_add_cells(  # noqa: CFQ002 — Explicit revision and redefinition facts determine result cells.
    name: DependencyName,
    resolved: ResolvedSource,
    plans: list[EntryPlan],
    *,
    previous_revision: SourceRevision | None,
    redefinition: bool,
    unchanged: bool,
) -> list[LogicCell]:
    """Build ordered dependency and installation results from operation facts."""
    outcome = Outcome.unchanged if unchanged else Outcome.replaced if redefinition else Outcome.added
    cells: list[LogicCell] = [
        DependencyCell(
            dependency=name,
            definition=resolved.dependency,
            revision=resolved.revision,
            outcome=outcome,
            previous_revision=previous_revision,
            redefinition=redefinition,
        )
    ]
    cells.extend(
        InstallationCell(
            dependency=name,
            definition=resolved.dependency,
            revision=resolved.revision,
            outcome=(
                Outcome.unchanged
                if not plan.changed and previous_revision is not None and previous_revision == resolved.revision
                else Outcome.updated
            ),
            previous_revision=previous_revision,
            redefinition=True,
            destination=plan.baseline.destination,
            source_path=plan.entry.source_path,
        )
        for plan in plans
    )
    return cells
