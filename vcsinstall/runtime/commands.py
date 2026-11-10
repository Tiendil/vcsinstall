from collections import Counter
from collections.abc import Callable
from functools import partial
from typing import BinaryIO

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.output import output
from llm_tool_cli.paths import ProjectPathId, join_path, project_path_parts
from llm_tool_cli.protocol.logic_cells.base import LogicCell

from vcsinstall.domain import (
    ContentKind,
    Dependency,
    DependencyName,
    SourcePath,
    SourceRevision,
)
from vcsinstall.filesystem import (
    ContentTree,
    FSClient,
    hashes,
)
from vcsinstall.hosting import HostingAdapter, get_hosting
from vcsinstall.runtime import errors
from vcsinstall.runtime.cells import (
    DependencyCell,
    DependencyListCell,
    DependencyOutdatedCell,
    InstallationCell,
    Operation,
    Outcome,
    RemovalCell,
    dependency_add_cells,
    entry_add_cell,
)
from vcsinstall.runtime.entities import DependencyPlan, DependencyRequest, EntryPlan, LocalContentPolicy, LocalStatus
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
    content_policy: LocalContentPolicy,
    observed: ContentTree | None = None,
) -> Result[EntryPlan]:
    destination = entry.destination
    previous_hashes = previous.file_hashes() if previous else {}
    source_hashes = source.file_hashes

    observed = observed if observed is not None else filesystem.inspect_tree(destination).unwrap()
    validate_no_local_content_conflicts(
        previous_hashes,
        source_hashes,
        files=observed.file_hashes,
        destination=destination,
        policy=content_policy,
    ).unwrap()
    _validate_destination_parents(destination, filesystem=filesystem).unwrap()

    baseline = _baseline(entry.dependency, entry, destination, source)
    if previous is not None and (
        previous.dependency == entry.dependency
        and previous.source_path == entry.source_path
        and not previous_hashes
        and not source_hashes
    ):
        baseline = previous

    return Ok(
        EntryPlan(
            entry=entry,
            source=source,
            observed=observed,
            previous=previous,
            baseline=baseline,
        )
    )


@unwrap_to_error
def _plan_entries(  # noqa: CCR001, CFQ002 — Keep source facts and independent absence/content policies together.
    workspace: Workspace,
    request: DependencyRequest,
    *,
    content: dict[SourcePath, ContentTree],
    resolved: ResolvedSource | None,
    source: SourceAdapter,
    allow_absent: bool,
    content_policy: LocalContentPolicy,
) -> Result[list[EntryPlan]]:
    plans = {plan.entry.destination: plan for plan in request.retained}
    filesystem = FSClient(root=workspace.project_root)
    for entry in workspace.config.entries_for_dependency(request.name):
        if entry.destination in plans:
            continue
        assert resolved is not None
        tree = content[entry.source_path]
        previous = workspace.lockfile.baseline_for_destination(entry.destination) if workspace.lockfile else None
        if (
            not allow_absent
            and tree.kind == ContentKind.absent
            and (
                previous is None
                or previous.dependency != entry.dependency
                or previous.source_path != entry.source_path
            )
        ):
            return Err(
                [
                    errors.SourcePathAbsent(
                        dependency=entry.dependency, source_path=entry.source_path, revision=resolved.revision
                    )
                ]
            )
        _validate_source_integrity(workspace, entry, tree, resolved=resolved, source=source).unwrap()
        plans[entry.destination] = _plan_entry(
            entry,
            source=tree,
            previous=previous,
            filesystem=filesystem,
            observed=request.observed.get(entry.destination),
            content_policy=content_policy,
        ).unwrap()
    return Ok([plan for destination, plan in sorted(plans.items())])


@unwrap_to_error
def _validate_source_integrity(
    workspace: Workspace,
    entry: Entry,
    tree: ContentTree,
    *,
    resolved: ResolvedSource,
    source: SourceAdapter,
) -> Result[None]:
    """Require retrieved content to agree with a baseline for the same immutable source state.

    Example: a guide recorded at revision A cannot have different bytes when
    retrieved again from A. A different source path or revision is a new state.
    """
    previous = workspace.lockfile.baseline_for_destination(entry.destination) if workspace.lockfile else None
    if previous is None or previous.source_path != entry.source_path:
        return Ok(None)
    assert workspace.lockfile is not None
    previous_lock = workspace.lockfile.dependencies[previous.dependency]
    if previous_lock.vcs != resolved.dependency.vcs or previous_lock.revision != resolved.revision:
        return Ok(None)
    repository = source.normalize_repository(previous_lock.repository, cwd=workspace.project_root).unwrap()
    if repository == resolved.dependency.repository and previous.file_hashes() != tree.file_hashes:
        return Err([errors.SourceIntegrityMismatch(destination=entry.destination)])
    return Ok(None)


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
        target = join_path(destination, file.path)
        source_path = SourcePath(join_path(ProjectPathId(plan.entry.source_path), file.path))
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


def _apply_dependency_plan(plan: DependencyPlan, *, filesystem: FSClient, source: SourceAdapter) -> Result[None]:
    """Apply prepared entry changes while retaining the plan's immutable source binding."""
    if plan.resolved is None:
        return Ok(None)
    return _apply_entry_changes(
        plan.entries, filesystem=filesystem, copy_file=partial(source.copy_file, plan.resolved)
    )


@unwrap_to_error
def _prepare_dependency_plan(  # noqa: CCR001 — Keep source inspection and baseline checks in one preparation scope.
    workspace: Workspace,
    request: DependencyRequest,
    resolved: ResolvedSource | None,
    *,
    source: SourceAdapter,
    content_policy: LocalContentPolicy,
    allow_absent: bool = True,
) -> Result[DependencyPlan]:
    """Prepare one shared lock and its entry changes without writing files or project records."""
    entries = workspace.config.entries_for_dependency(request.name)
    retained = {plan.entry.destination for plan in request.retained}
    paths = tuple(dict.fromkeys(entry.source_path for entry in entries if entry.destination not in retained))
    assert request.lock is not None or resolved is not None
    definition = resolved.dependency if resolved is not None else request.definition
    revision = resolved.revision if resolved is not None else request.lock.revision  # type: ignore[union-attr]
    if resolved is not None:
        source.validate_revision_lock(definition, revision).unwrap()
    content = source.inspect(resolved, paths).unwrap() if paths and resolved is not None else {}
    plans = _plan_entries(
        workspace,
        request,
        content=content,
        resolved=resolved,
        source=source,
        allow_absent=allow_absent,
        content_policy=content_policy,
    ).unwrap()
    previous_lock = workspace.lockfile.dependencies.get(request.name) if workspace.lockfile else None
    lock = DependencyLock(**definition.model_dump(), revision=revision)
    lock_unchanged = False
    if previous_lock is not None and previous_lock.vcs == lock.vcs:
        repository = source.normalize_repository(previous_lock.repository, cwd=workspace.project_root).unwrap()
        lock_unchanged = repository is not None and previous_lock.replace(repository=repository) == lock
        if lock_unchanged:
            lock = previous_lock
    recorded = workspace.lockfile.entries_for_dependency(request.name) if workspace.lockfile else ()
    return Ok(
        DependencyPlan(
            name=request.name,
            definition=definition,
            resolved=resolved,
            lock=lock,
            entries=plans,
            previous_revision=previous_lock.revision if previous_lock else None,
            lock_unchanged=lock_unchanged,
            unchanged=(
                lock_unchanged
                and {baseline.destination for baseline in recorded} == {entry.destination for entry in entries}
                and all(not plan.changed for plan in plans)
            ),
        )
    )


def _lockfile_with_plans(workspace: Workspace, plans: list[DependencyPlan]) -> Lockfile:
    """Record the prepared shared locks and baselines while preserving unrelated state and record order."""
    lockfile = workspace.lockfile or Lockfile(version=1)
    for plan in plans:
        lockfile = lockfile.with_dependency(plan.name, plan.lock).with_baselines(
            entry.baseline for entry in plan.entries
        )
    return lockfile


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
        request = DependencyRequest(name=name, definition=dependency)
        plan = _prepare_dependency_plan(
            workspace, request, resolved, source=source, content_policy=LocalContentPolicy.protect
        ).unwrap()
        config = workspace.config.with_dependency(name, resolved.dependency)
        lockfile = _lockfile_with_plans(workspace, [plan])
        serialized = serialize_workspace_changes(workspace, config, lockfile)

        _apply_dependency_plan(
            plan, filesystem=FSClient(root=workspace.project_root, readonly=False), source=source
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
    _validate_source_integrity(workspace, entry, tree, resolved=resolved, source=source).unwrap()
    filesystem = FSClient(root=workspace.project_root, readonly=False)
    plan = _plan_entry(
        entry, source=tree, previous=previous, filesystem=filesystem, content_policy=LocalContentPolicy.protect
    ).unwrap()
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
def _prepare_dependency_request(
    workspace: Workspace, name: DependencyName, *, source: SourceAdapter
) -> Result[DependencyRequest]:
    """Prepare normalized source intent, retaining a matching lock's effective selector when available."""
    matching = _prepare_dependency_lock(workspace, name, source=source)
    dependency_lock = None
    if not matching.is_err(workspace_errors.DependencyLockRequired):
        definition = Dependency.model_validate(matching.unwrap().model_dump(exclude={"revision"}))
        assert workspace.lockfile is not None
        dependency_lock = workspace.lockfile.dependencies[name]
    else:
        declared = workspace.config.dependencies[name]
        repository = source.normalize_repository(declared.repository, cwd=workspace.project_root).unwrap()
        if repository is None:
            return Err([InvalidArguments(reason=f"Unsupported repository locator for dependency {name!r}.")])
        definition = declared.replace(repository=repository)
    source.validate_input(definition).unwrap()
    return Ok(DependencyRequest(name=name, definition=definition, lock=dependency_lock))


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
def _prepare_local_dependency(workspace: Workspace, name: DependencyName) -> Result[DependencyRequest]:
    """Prepare source identity and a matching lock for inspection without repository access."""
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

    return Ok(DependencyRequest(name=name, definition=definition, lock=locked))


@unwrap_to_error
def _dependency_list_cell(
    workspace: Workspace, name: DependencyName, *, filesystem: FSClient
) -> Result[DependencyListCell]:
    request = _prepare_local_dependency(workspace, name).unwrap()
    entries = workspace.config.entries_for_dependency(name)
    counts = Counter(
        _inspect_entry_status(workspace, entry, filesystem=filesystem, locked=request.lock is not None).unwrap()
        for entry in entries
    )
    return Ok(
        DependencyListCell(
            dependency=name,
            definition=request.definition,
            revision=request.lock.revision if request.lock is not None else None,
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
def entry_list(destinations: tuple[ProjectPathId, ...] = ()) -> Result[None]:
    """Report selected declarations and their local status without source access or project writes."""
    workspace = active_workspace.get()
    declared = {entry.destination: entry for entry in workspace.config.entries}
    selected = set(destinations) if destinations else set(declared)
    unknown = sorted(selected - declared.keys())
    if unknown:
        return Err([InvalidArguments(reason=f"Unknown entry destination {destination!r}.") for destination in unknown])

    entries = [declared[destination] for destination in sorted(selected)]
    requests = {
        name: _prepare_local_dependency(workspace, name).unwrap()
        for name in sorted({entry.dependency for entry in entries})
    }
    filesystem = FSClient(root=workspace.project_root)
    cells: list[LogicCell] = []
    for entry in entries:
        request = requests[entry.dependency]
        status = _inspect_entry_status(
            workspace, entry, filesystem=filesystem, locked=request.lock is not None
        ).unwrap()
        revision = request.lock.revision if request.lock is not None and status != LocalStatus.unlocked else None
        cells.append(
            InstallationCell(
                operation=Operation.list,
                dependency=entry.dependency,
                definition=request.definition,
                revision=revision,
                destination=entry.destination,
                source_path=entry.source_path,
                status=status,
            )
        )
    output.get().emit(cells).unwrap()
    return Ok(None)


@unwrap_to_error
def _dependency_current_revision(
    locked: DependencyLock,
    *,
    source: SourceAdapter,
) -> Result[SourceRevision]:
    definition = Dependency.model_validate(locked.model_dump(exclude={"revision"}))
    source.validate_input(definition).unwrap()
    hosting = get_hosting(definition).unwrap()
    with hosting.acquire(definition) as acquired:
        resolved = source.resolve(definition, acquired.unwrap()).unwrap()
        return Ok(resolved.revision)


@unwrap_to_error
def dependency_outdated(names: tuple[DependencyName, ...] = ()) -> Result[None]:
    """Compare selected matching dependency locks with current source revisions without project writes."""
    workspace = active_workspace.get()
    selected = sorted(set(names) if names else workspace.config.dependencies)
    unknown = [name for name in selected if name not in workspace.config.dependencies]
    if unknown:
        return Err([InvalidArguments(reason=f"Unknown dependency {name!r}.") for name in unknown])

    sources = {name: get_adapter(workspace.config.dependencies[name].vcs) for name in selected}
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
            _dependency_current_revision(locked, source=sources[name])
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
def entry_add(
    entry: Entry,
    *,
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

    source = get_adapter(workspace.config.dependencies[entry.dependency].vcs)
    locked = _prepare_dependency_lock(workspace, entry.dependency, source=source).unwrap()
    dependency = Dependency.model_validate(locked.model_dump(exclude={"revision"}))
    source.validate_input(dependency).unwrap()
    hosting = get_hosting(dependency).unwrap()

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
