from collections.abc import Callable
from functools import partial
from typing import BinaryIO

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.output import output
from llm_tool_cli.paths import ProjectPathId, project_path_parts

from vcsinstall.domain import (
    ContentKind,
    Dependency,
    DependencyName,
    SourcePath,
)
from vcsinstall.filesystem import (
    ContentTree,
    FSClient,
    hashes,
)
from vcsinstall.hosting import HostingAdapter
from vcsinstall.runtime import errors
from vcsinstall.runtime.cells import dependency_add_cells, entry_add_cell
from vcsinstall.runtime.entities import EntryPlan
from vcsinstall.runtime.validation import (
    validate_entry_redefinition,
    validate_freshness,
    validate_no_local_content_conflicts,
    validate_redefinition,
)
from vcsinstall.sources import (
    ResolvedSource,
    SourceAdapter,
)
from vcsinstall.workspace import (
    AbsentBaseline,
    Config,
    DependencyLock,
    DirectoryBaseline,
    Entry,
    FileBaseline,
    InstallationBaseline,
    Lockfile,
    ManifestFile,
    Workspace,
    active_workspace,
)
from vcsinstall.workspace import errors as workspace_errors
from vcsinstall.workspace import serialize_workspace_changes, write_workspace_files


def _baseline(
    name: DependencyName, entry: Entry, destination: ProjectPathId, tree: ContentTree
) -> InstallationBaseline:
    file_hashes = tree.file_hashes
    if tree.kind == ContentKind.file:
        return FileBaseline(
            dependency=name,
            source_path=ProjectPathId(entry.source_path),
            destination=destination,
            hash=next(iter(file_hashes.values())),
        )
    if tree.kind == ContentKind.directory:
        return DirectoryBaseline(
            dependency=name,
            source_path=ProjectPathId(entry.source_path),
            destination=destination,
            hash=hashes.hash_files(file_hashes),
            files=tuple(ManifestFile(path=path, hash=digest) for path, digest in file_hashes.items()),
        )
    return AbsentBaseline(dependency=name, source_path=ProjectPathId(entry.source_path), destination=destination)


def _join_path(root: ProjectPathId, relative: ProjectPathId) -> ProjectPathId:
    suffix = relative.removeprefix("@/")
    return ProjectPathId(root.rstrip("/") + "/" + suffix) if suffix else root


@unwrap_to_error
def _validate_destination_parents(destination: ProjectPathId, *, filesystem: FSClient) -> Result[None]:
    """Inspect required parent positions and reject regular-file obstructions.

    Example: a regular file at '@/docs' blocks destination '@/docs/shared/guide'.
    A directory or a missing parent passes; the destination's own content is
    checked separately when planning the entry.
    """
    parts = project_path_parts(destination)
    for end in range(1, len(parts)):
        parent = ProjectPathId("@/" + "/".join(parts[:end]))
        if filesystem.inspect_kind(parent).unwrap() == ContentKind.file:
            return Err([errors.LocalConflict(destination=parent)])
    return Ok(None)


@unwrap_to_error
def _plan_entry(
    entry: Entry,
    *,
    source: ContentTree,
    previous: InstallationBaseline | None,
    filesystem: FSClient,
) -> Result[EntryPlan]:
    destination = entry.destination
    previous_hashes = previous.file_hashes() if previous else {}
    source_hashes = source.file_hashes

    observed = filesystem.inspect_tree(destination).unwrap()
    validate_no_local_content_conflicts(
        previous_hashes,
        source_hashes,
        files=observed.file_hashes,
        destination=destination,
    ).unwrap()
    _validate_destination_parents(destination, filesystem=filesystem).unwrap()

    return Ok(
        EntryPlan(
            entry=entry,
            source=source,
            observed=observed,
            previous=previous,
            baseline=_baseline(entry.dependency, entry, destination, source),
        )
    )


@unwrap_to_error
def _plan_entries(
    workspace: Workspace,
    name: DependencyName,
    *,
    content: dict[SourcePath, ContentTree],
    filesystem: FSClient,
    entries: dict[ProjectPathId, Entry],
) -> Result[list[EntryPlan]]:

    recorded = (
        {baseline.destination: baseline for baseline in workspace.lockfile.entries_for_dependency(name)}
        if workspace.lockfile
        else {}
    )
    return Ok(
        [
            _plan_entry(
                entry,
                source=content[entry.source_path],
                previous=recorded[destination],
                filesystem=filesystem,
            ).unwrap()
            for destination, entry in sorted(entries.items())
        ]
    )


@unwrap_to_error
def _apply_entry_change(
    plan: EntryPlan,
    *,
    filesystem: FSClient,
    copy_file: Callable[[SourcePath, BinaryIO], Result[None]],
) -> Result[None]:

    destination = plan.baseline.destination
    if plan.observed.file_hashes == plan.source.file_hashes:
        return Ok(None)
    if plan.observed.kind != ContentKind.absent:
        filesystem.remove(destination).unwrap()
    for file in plan.source.files:
        target = _join_path(destination, file.path)
        source_path = SourcePath(_join_path(ProjectPathId(plan.entry.source_path), file.path))
        filesystem.write_stream(target, partial(copy_file, source_path), executable=file.executable).unwrap()
    return Ok(None)


def _apply_entry_changes(
    plans: list[EntryPlan],
    *,
    filesystem: FSClient,
    copy_file: Callable[[SourcePath, BinaryIO], Result[None]],
) -> Result[None]:
    for plan in plans:
        result = _apply_entry_change(plan, filesystem=filesystem, copy_file=copy_file)
        if result.is_err():
            return Err([errors.InstallationWriteFailed(), *result.unwrap_err()])
    return Ok(None)


def _updated_dependency_records(
    workspace: Workspace,
    name: DependencyName,
    resolved: ResolvedSource,
    plans: list[EntryPlan],
) -> tuple[Config, Lockfile]:  # noqa: TAE002 — Both updated models remain local to this operation.
    new_lock = DependencyLock(**resolved.dependency.model_dump(), revision=resolved.revision)
    lockfile = workspace.lockfile or Lockfile(version=1)
    replacements = {plan.baseline.destination: plan.baseline for plan in plans}
    lockfile = lockfile.replace(
        dependencies=lockfile.dependencies | {name: new_lock},
        entries=tuple(replacements.get(entry.destination, entry) for entry in lockfile.entries),
    )
    config = workspace.config.replace(dependencies=workspace.config.dependencies | {name: resolved.dependency})
    return config, lockfile


@unwrap_to_error
def _apply_dependency(
    workspace: Workspace,
    name: DependencyName,
    source: SourceAdapter,
    resolved: ResolvedSource,
    *,
    entries: dict[ProjectPathId, Entry],
) -> Result[None]:
    source.validate_revision_lock(resolved.dependency, resolved.revision).unwrap()
    previous_lock = workspace.lockfile.dependencies.get(name) if workspace.lockfile else None
    was_declared = name in workspace.config.dependencies
    filesystem = FSClient(root=workspace.project_root, readonly=False)
    paths = tuple(entry.source_path for entry in entries.values())
    content = source.inspect(resolved, paths).unwrap() if paths else {}
    plans = _plan_entries(workspace, name, content=content, filesystem=filesystem, entries=entries).unwrap()
    config, lockfile = _updated_dependency_records(workspace, name, resolved, plans)
    unchanged = (
        config == workspace.config and lockfile == workspace.lockfile and all(not plan.changed for plan in plans)
    )
    serialized_config_and_lockfile = serialize_workspace_changes(workspace, config, lockfile)
    _apply_entry_changes(plans, filesystem=filesystem, copy_file=partial(source.copy_file, resolved)).unwrap()
    write_workspace_files(workspace, serialized_config_and_lockfile).unwrap()
    cells = dependency_add_cells(
        name,
        resolved,
        plans,
        previous_revision=previous_lock.revision if previous_lock else None,
        redefinition=was_declared,
        unchanged=unchanged,
    )
    output.get().emit(cells).unwrap()
    return Ok(None)


@unwrap_to_error
def dependency_add(  # noqa: CFQ002 — Explicit source intent and independently injected adapters.
    name: DependencyName,
    dependency: Dependency,
    *,
    source: SourceAdapter,
    hosting: HostingAdapter,
    redefine: bool = False,
) -> Result[None]:
    workspace = active_workspace.get()
    validate_redefinition(name, exists=name in workspace.config.dependencies, redefine=redefine).unwrap()
    entries = {entry.destination: entry for entry in workspace.config.entries_for_dependency(name)}
    workspace.validate_no_orphaned_baselines(name).unwrap()
    workspace.validate_no_missing_baselines(name).unwrap()
    filesystem = FSClient(root=workspace.project_root)
    for entry in entries.values():
        filesystem.resolve_path(entry.destination).unwrap()
    source.validate_input(dependency).unwrap()

    with hosting.acquire(dependency) as acquired:
        resolved = source.resolve(dependency, acquired.unwrap()).unwrap()
        return _apply_dependency(workspace, name, source, resolved, entries=entries)


@unwrap_to_error
def _install_added_entry(
    workspace: Workspace,
    entry: Entry,
    resolved: ResolvedSource,
    *,
    source: SourceAdapter,
    previous: InstallationBaseline | None,
) -> Result[None]:
    tree = source.inspect(resolved, (entry.source_path,)).unwrap()[entry.source_path]
    if tree.kind == ContentKind.absent:
        return Err(
            [
                errors.SourcePathAbsent(
                    dependency=entry.dependency,
                    source_path=entry.source_path,
                    revision=resolved.revision,
                )
            ]
        )
    if previous is not None and (
        previous.dependency == entry.dependency
        and previous.source_path == entry.source_path
        and previous.file_hashes() != tree.file_hashes
    ):
        return Err([errors.SourceIntegrityMismatch(destination=entry.destination)])
    filesystem = FSClient(root=workspace.project_root, readonly=False)
    plan = _plan_entry(entry, source=tree, previous=previous, filesystem=filesystem).unwrap()
    declarations = {item.destination: item for item in workspace.config.entries}
    declarations[entry.destination] = entry
    config = workspace.config.replace(entries=tuple(declarations.values()))
    assert workspace.lockfile is not None
    baselines = {item.destination: item for item in workspace.lockfile.entries}
    baselines[entry.destination] = plan.baseline
    lockfile = workspace.lockfile.replace(entries=tuple(baselines.values()))
    serialized = serialize_workspace_changes(workspace, config, lockfile)
    _apply_entry_changes([plan], filesystem=filesystem, copy_file=partial(source.copy_file, resolved)).unwrap()
    write_workspace_files(workspace, serialized).unwrap()
    unchanged = config == workspace.config and lockfile == workspace.lockfile and not plan.changed
    output.get().emit([entry_add_cell(entry, resolved, unchanged=unchanged)]).unwrap()
    return Ok(None)


@unwrap_to_error
def _prepare_dependency_lock(
    workspace: Workspace, name: DependencyName, *, source: SourceAdapter
) -> Result[DependencyLock]:
    """Prepare repository identities for matching and acquisition without changing loaded state."""
    declared = workspace.config.dependencies.get(name)
    recorded = workspace.lockfile.dependencies.get(name) if workspace.lockfile else None
    if declared is None or recorded is None or declared.vcs != recorded.vcs:
        return workspace.require_matching_dependency_lock(name, source=source)
    repository = source.normalize_repository(declared.repository, cwd=workspace.project_root).unwrap()
    locked_repository = source.normalize_repository(recorded.repository, cwd=workspace.project_root).unwrap()
    if repository is None or locked_repository is None:
        return Err([workspace_errors.DependencyLockRequired(path=workspace.lockfile_path, dependency=name)])
    assert workspace.lockfile is not None
    prepared = workspace.replace(
        config=workspace.config.replace(
            dependencies=workspace.config.dependencies
            | {
                name: declared.replace(repository=repository),
            }
        ),
        lockfile=workspace.lockfile.replace(
            dependencies=workspace.lockfile.dependencies
            | {
                name: recorded.replace(repository=locked_repository),
            }
        ),
    )
    return prepared.require_matching_dependency_lock(name, source=source)


@unwrap_to_error
def entry_add(  # noqa: CFQ002 — One entry and independent adapters with explicit operation permissions.
    entry: Entry,
    *,
    source: SourceAdapter,
    hosting: HostingAdapter,
    redefine: bool = False,
    allow_outdated: bool = False,
) -> Result[None]:
    workspace = active_workspace.get()
    if entry.dependency not in workspace.config.dependencies:
        return Err([InvalidArguments(reason=f"Unknown dependency {entry.dependency!r}.")])
    declared = workspace.config.entry_for_destination(entry.destination)
    validate_entry_redefinition(entry.destination, exists=declared is not None, redefine=redefine).unwrap()
    workspace.validate_destination_ownership(entry.destination).unwrap()
    FSClient(root=workspace.project_root).resolve_path(entry.destination).unwrap()
    previous: InstallationBaseline | None = None
    if declared is not None:
        previous = workspace.require_entry_baseline(declared).unwrap()
    elif workspace.lockfile is not None:
        previous = workspace.lockfile.baseline_for_destination(entry.destination)
    locked = _prepare_dependency_lock(workspace, entry.dependency, source=source).unwrap()
    dependency = Dependency.model_validate(locked.model_dump(exclude={"revision"}))
    source.validate_input(dependency).unwrap()
    with hosting.acquire(dependency, revision=locked.revision) as acquired:
        path = acquired.unwrap()
        current = source.resolve(dependency, path).unwrap()
        validate_freshness(
            entry.dependency,
            selector=dependency.selector,
            locked_revision=locked.revision,
            current_revision=current.revision,
            allow_outdated=allow_outdated,
        ).unwrap()
        resolved = ResolvedSource(dependency=dependency, path=path, revision=locked.revision)
        return _install_added_entry(workspace, entry, resolved, source=source, previous=previous)
