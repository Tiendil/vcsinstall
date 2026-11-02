from collections import Counter
from collections.abc import Callable
from functools import partial
from typing import BinaryIO

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.output import output
from llm_tool_cli.paths import ProjectPathId, project_path_parts
from llm_tool_cli.protocol.logic_cells.base import LogicCell

from vcsinstall.domain import (
    ContentKind,
    Dependency,
    DependencyName,
    SourcePath,
    SourceRevision,
    VcsId,
)
from vcsinstall.filesystem import (
    ContentTree,
    FSClient,
    hashes,
)
from vcsinstall.hosting import HostingAdapter
from vcsinstall.runtime import errors
from vcsinstall.runtime.cells import (
    DependencyCell,
    DependencyListCell,
    DependencyOutdatedCell,
    Operation,
    Outcome,
    RemovalCell,
    dependency_add_cells,
    entry_add_cell,
)
from vcsinstall.runtime.entities import DependencyPlan, EntryPlan, LocalStatus
from vcsinstall.runtime.validation import (
    local_content_status,
    validate_entry_redefinition,
    validate_freshness,
    validate_no_local_content_conflicts,
    validate_redefinition,
    validate_removal_content,
)
from vcsinstall.sources import (
    ResolvedSource,
    SourceAdapter,
    get_adapter,
)
from vcsinstall.workspace import (
    AbsentBaseline,
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


@unwrap_to_error
def _prepare_dependency_plan(  # noqa: CCR001 — Keep source inspection and baseline checks in one preparation scope.
    workspace: Workspace,
    name: DependencyName,
    resolved: ResolvedSource,
    *,
    source: SourceAdapter,
) -> Result[DependencyPlan]:
    """Prepare one shared lock and its entry changes without writing files or project records."""
    source.validate_revision_lock(resolved.dependency, resolved.revision).unwrap()
    entries = {entry.destination: entry for entry in workspace.config.entries_for_dependency(name)}
    paths = tuple(entry.source_path for entry in entries.values())
    content = source.inspect(resolved, paths).unwrap() if paths else {}
    plans = _plan_entries(
        workspace,
        name,
        content=content,
        filesystem=FSClient(root=workspace.project_root),
        entries=entries,
    ).unwrap()
    # Empty structural directories and expected absence have the same zero-file
    # content; their existence alone does not require changing a baseline.
    plans = [
        (
            plan.replace(baseline=plan.previous)
            if (
                plan.previous is not None
                and plan.previous.source_path == plan.entry.source_path
                and not plan.previous.file_hashes()
                and not plan.source.file_hashes
            )
            else plan
        )
        for plan in plans
    ]
    previous_lock = workspace.lockfile.dependencies.get(name) if workspace.lockfile else None
    lock = DependencyLock(**resolved.dependency.model_dump(), revision=resolved.revision)
    lock_unchanged = False
    if previous_lock is not None and previous_lock.vcs == lock.vcs:
        repository = source.normalize_repository(previous_lock.repository, cwd=workspace.project_root).unwrap()
        lock_unchanged = repository is not None and previous_lock.replace(repository=repository) == lock
        if lock_unchanged:
            lock = previous_lock
        if repository == resolved.dependency.repository and previous_lock.revision == resolved.revision:
            for plan in plans:
                previous = plan.previous
                if (
                    previous is not None
                    and previous.source_path == plan.entry.source_path
                    and previous.file_hashes() != plan.source.file_hashes
                ):
                    return Err([errors.SourceIntegrityMismatch(destination=plan.entry.destination)])
    return Ok(
        DependencyPlan(
            name=name,
            resolved=resolved,
            lock=lock,
            entries=plans,
            previous_revision=previous_lock.revision if previous_lock else None,
            unchanged=lock_unchanged and all(not plan.changed for plan in plans),
        )
    )


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
    was_declared = name in workspace.config.dependencies
    validate_redefinition(name, exists=was_declared, redefine=redefine).unwrap()
    workspace.validate_no_orphaned_baselines(name).unwrap()
    workspace.validate_no_missing_baselines(name).unwrap()
    filesystem = FSClient(root=workspace.project_root)
    for entry in workspace.config.entries_for_dependency(name):
        filesystem.resolve_path(entry.destination).unwrap()
    source.validate_input(dependency).unwrap()

    with hosting.acquire(dependency) as acquired:
        resolved = source.resolve(dependency, acquired.unwrap()).unwrap()
        plan = _prepare_dependency_plan(workspace, name, resolved, source=source).unwrap()
        config = workspace.config.with_dependency(name, resolved.dependency)
        lockfile = (workspace.lockfile or Lockfile(version=1)).with_dependency(name, plan.lock)
        lockfile = lockfile.with_baselines(entry.baseline for entry in plan.entries)
        serialized = serialize_workspace_changes(workspace, config, lockfile)

        _apply_entry_changes(
            plan.entries,
            filesystem=FSClient(root=workspace.project_root, readonly=False),
            copy_file=partial(source.copy_file, resolved),
        ).unwrap()
        write_workspace_files(workspace, serialized).unwrap()
        output.get().emit(
            dependency_add_cells(
                plan,
                redefinition=was_declared,
                unchanged=config == workspace.config and plan.unchanged,
            )
        ).unwrap()
    return Ok(None)


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
    config = workspace.config.with_entry(entry)
    assert workspace.lockfile is not None
    lockfile = workspace.lockfile.with_baselines((plan.baseline,))
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
        config=workspace.config.with_dependency(name, declared.replace(repository=repository)),
        lockfile=workspace.lockfile.with_dependency(name, recorded.replace(repository=locked_repository)),
    )
    return prepared.require_matching_dependency_lock(name, source=source)


@unwrap_to_error
def _inspect_entry_status(
    workspace: Workspace, entry: Entry, *, filesystem: FSClient, locked: bool
) -> Result[LocalStatus]:
    observed = filesystem.inspect_tree(entry.destination).unwrap()
    if not locked:
        return Ok(LocalStatus.unlocked)

    result = workspace.require_matching_entry_baseline(entry)
    if result.is_err(workspace_errors.MissingBaseline) or result.is_err(workspace_errors.BaselineMismatch):
        return Ok(LocalStatus.unlocked)
    baseline = result.unwrap()

    parts = project_path_parts(entry.destination)
    for end in range(1, len(parts)):
        parent = ProjectPathId("@/" + "/".join(parts[:end]))
        if filesystem.inspect_kind(parent).unwrap() == ContentKind.file:
            return Ok(LocalStatus.modified)
    return Ok(local_content_status(baseline.file_hashes(), files=observed.file_hashes))


@unwrap_to_error
def _dependency_list_cell(
    workspace: Workspace, name: DependencyName, *, filesystem: FSClient
) -> Result[DependencyListCell]:
    definition = workspace.config.dependencies[name]
    source = get_adapter(definition.vcs)
    lock_result = _prepare_dependency_lock(workspace, name, source=source)
    locked = None if lock_result.is_err(workspace_errors.DependencyLockRequired) else lock_result.unwrap()
    repository = (
        locked.repository
        if locked is not None
        else source.normalize_repository(definition.repository, cwd=workspace.project_root).unwrap()
    )
    definition = definition.replace(repository=repository or definition.repository)
    if definition.selector is None and locked is not None:
        definition = definition.replace(selector=locked.selector)

    entries = workspace.config.entries_for_dependency(name)
    counts = Counter(
        _inspect_entry_status(workspace, entry, filesystem=filesystem, locked=locked is not None).unwrap()
        for entry in entries
    )
    return Ok(
        DependencyListCell(
            dependency=name,
            definition=definition,
            revision=locked.revision if locked is not None else None,
            entries=len(entries),
            modified=counts[LocalStatus.modified],
            missing=counts[LocalStatus.missing],
            unlocked=counts[LocalStatus.unlocked],
        )
    )


@unwrap_to_error
def dependency_list() -> Result[None]:
    """Report declared dependencies and local entry statistics without source access."""
    workspace = active_workspace.get()
    filesystem = FSClient(root=workspace.project_root)
    cells: list[LogicCell] = [
        _dependency_list_cell(workspace, name, filesystem=filesystem).unwrap()
        for name in sorted(workspace.config.dependencies)
    ]
    output.get().emit(cells).unwrap()
    return Ok(None)


@unwrap_to_error
def _dependency_current_revision(
    locked: DependencyLock,
    *,
    source: SourceAdapter,
    hosting_for: Callable[[VcsId], Result[HostingAdapter]],
) -> Result[SourceRevision]:
    definition = Dependency.model_validate(locked.model_dump(exclude={"revision"}))
    source.validate_input(definition).unwrap()
    hosting = hosting_for(definition.vcs).unwrap()
    with hosting.acquire(definition) as acquired:
        resolved = source.resolve(definition, acquired.unwrap()).unwrap()
        return Ok(resolved.revision)


@unwrap_to_error
def dependency_outdated(
    names: tuple[DependencyName, ...] = (),
    *,
    source_for: Callable[[VcsId], SourceAdapter],
    hosting_for: Callable[[VcsId], Result[HostingAdapter]],
) -> Result[None]:
    """Compare selected matching dependency locks with current source revisions without project writes."""
    workspace = active_workspace.get()
    selected = sorted(set(names) if names else workspace.config.dependencies)
    unknown = [name for name in selected if name not in workspace.config.dependencies]
    if unknown:
        return Err([InvalidArguments(reason=f"Unknown dependency {name!r}.") for name in unknown])

    sources = {name: source_for(workspace.config.dependencies[name].vcs) for name in selected}
    locks = {
        name: (
            _prepare_dependency_lock(workspace, name, source=sources[name])
            .map_err(lambda problems: [errors.DependencyCheckFailed(dependency=name), *problems])
            .unwrap()
        )
        for name in selected
    }

    cells: list[LogicCell] = []
    for name, locked in locks.items():
        current_revision = (
            _dependency_current_revision(locked, source=sources[name], hosting_for=hosting_for)
            .map_err(lambda problems: [errors.DependencyCheckFailed(dependency=name), *problems])
            .unwrap()
        )
        if current_revision != locked.revision:
            cells.append(
                DependencyOutdatedCell(
                    dependency=name,
                    definition=Dependency.model_validate(locked.model_dump(exclude={"revision"})),
                    revision=locked.revision,
                    current_revision=current_revision,
                )
            )
    output.get().emit(cells).unwrap()
    return Ok(None)


@unwrap_to_error
def dependency_remove(  # noqa: CCR001 — Keep removal validation, prior-lock reporting, and persistence together.
    name: DependencyName,
) -> Result[None]:
    """Remove an unused declaration and its recorded state without accessing content."""
    workspace = active_workspace.get()
    definition = workspace.config.dependencies.get(name)
    if definition is None:
        return Err([InvalidArguments(reason=f"Unknown dependency {name!r}.")])
    entries = workspace.config.entries_for_dependency(name)
    if entries:
        destinations = ", ".join(sorted(entry.destination for entry in entries))
        return Err(
            [
                InvalidArguments(
                    reason=f"Dependency {name!r} still has declared entries: {destinations}.",
                    ways_to_fix=["Remove or reassign these entries before removing the dependency."],
                )
            ]
        )

    workspace.validate_no_reassigned_baselines(name).unwrap()
    source = get_adapter(definition.vcs)
    lock_result = _prepare_dependency_lock(workspace, name, source=source)
    locked = None if lock_result.is_err(workspace_errors.DependencyLockRequired) else lock_result.unwrap()
    repository = (
        locked.repository
        if locked is not None
        else source.normalize_repository(definition.repository, cwd=workspace.project_root).unwrap()
    )
    definition = definition.replace(repository=repository or definition.repository)
    if definition.selector is None and locked is not None:
        definition = definition.replace(selector=locked.selector)

    config = workspace.config.without_dependency(name)
    lockfile = workspace.lockfile.without_dependency(name) if workspace.lockfile is not None else None
    serialized = serialize_workspace_changes(workspace, config, lockfile)
    write_workspace_files(workspace, serialized).unwrap()
    output.get().emit(
        [
            DependencyCell(
                operation=Operation.dependency_remove,
                dependency=name,
                definition=definition,
                revision=locked.revision if locked is not None else None,
                outcome=Outcome.removed,
            )
        ]
    ).unwrap()
    return Ok(None)


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


@unwrap_to_error
def _prepare_entry_removal(
    workspace: Workspace, entry: Entry, *, filesystem: FSClient, force: bool
) -> Result[ContentTree]:
    workspace.validate_destination_ownership(entry.destination).unwrap()
    _validate_destination_parents(entry.destination, filesystem=filesystem).unwrap()
    observed = filesystem.inspect_tree(entry.destination).unwrap()
    if observed.kind != ContentKind.absent and not force:
        source = get_adapter(workspace.config.dependencies[entry.dependency].vcs)
        _prepare_dependency_lock(workspace, entry.dependency, source=source).unwrap()
        baseline = workspace.require_matching_entry_baseline(entry).unwrap()
        validate_removal_content(
            baseline.file_hashes(), files=observed.file_hashes, destination=entry.destination
        ).unwrap()
    return Ok(observed)


@unwrap_to_error
def entry_remove(  # noqa: CCR001 — Keep whole-selection preparation before filesystem effects and persistence.
    destinations: tuple[ProjectPathId, ...], *, keep_files: bool = False, force: bool = False
) -> Result[None]:
    workspace = active_workspace.get()
    if not destinations:
        return Err([InvalidArguments(reason="Select at least one declared entry root to remove.")])
    entries: list[Entry] = []
    for destination in sorted(set(destinations)):
        entry = workspace.config.entry_for_destination(destination)
        if entry is None:
            return Err([InvalidArguments(reason=f"Unknown entry destination {destination!r}.")])
        entries.append(entry)

    filesystem = FSClient(root=workspace.project_root, readonly=False)
    removals: list[ProjectPathId] = []
    for entry in entries:
        filesystem.resolve_path(entry.destination).unwrap()
        if not keep_files:
            observed = _prepare_entry_removal(workspace, entry, filesystem=filesystem, force=force).unwrap()
            if observed.kind != ContentKind.absent:
                removals.append(entry.destination)

    config = workspace.config.without_entries(destinations)
    lockfile = workspace.lockfile.without_entries(destinations) if workspace.lockfile is not None else None
    serialized = serialize_workspace_changes(workspace, config, lockfile)
    for destination in removals:
        result = filesystem.remove(destination)
        if result.is_err():
            return Err([errors.RemovalWriteFailed(destination=destination), *result.unwrap_err()])
    write_workspace_files(workspace, serialized).unwrap()
    output.get().emit(
        [
            RemovalCell(dependency=entry.dependency, destination=entry.destination, keep_files=keep_files)
            for entry in entries
        ]
    ).unwrap()
    return Ok(None)
