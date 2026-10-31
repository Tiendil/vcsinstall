from collections.abc import Callable
from functools import partial
from typing import BinaryIO

from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.output import output
from llm_tool_cli.paths import ProjectPathId, normalize_path, project_path_parts, resolve_root_anchored_path

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
from vcsinstall.runtime.cells import dependency_add_cells
from vcsinstall.runtime.entities import EntryPlan
from vcsinstall.runtime.validation import validate_no_local_content_conflicts, validate_redefinition
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
    serialize_workspace_changes,
    validate_destinations,
    write_workspace_files,
)


@unwrap_to_error
def _dependency_destinations(workspace: Workspace, name: DependencyName) -> Result[dict[ProjectPathId, Entry]]:
    """Validate all destinations and select this dependency's canonical entries."""
    entries = {}
    reserved = tuple(
        normalize_path(str(path), workspace.project_root).unwrap()
        for path in (workspace.config_path, workspace.lockfile_path)
    )
    for collection in (workspace.config.entries, workspace.lockfile.entries if workspace.lockfile else ()):
        paths = tuple(entry.destination for entry in collection)
        validate_destinations(paths, reserved=reserved).unwrap()
        for path in paths:
            resolve_root_anchored_path(path, workspace.project_root).unwrap()
    for entry in workspace.config.entries_for_dependency(name):
        entries[entry.destination] = entry
    return Ok(entries)


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
    previous: InstallationBaseline,
    filesystem: FSClient,
) -> Result[EntryPlan]:
    destination = entry.destination
    previous_hashes = previous.file_hashes()
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
    entries = _dependency_destinations(workspace, name).unwrap()
    workspace.validate_no_orphaned_baselines(name).unwrap()
    workspace.validate_no_missing_baselines(name).unwrap()
    source.validate_input(dependency).unwrap()

    with hosting.acquire(dependency) as acquired:
        resolved = source.resolve(dependency, acquired.unwrap()).unwrap()
        return _apply_dependency(workspace, name, source, resolved, entries=entries)
