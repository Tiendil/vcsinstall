"""Return complete local entry changes to an explicitly supplied content root."""

from functools import partial
from pathlib import Path

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config.runtime import runtime_config
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.output import output
from llm_tool_cli.paths import (
    PathInput,
    ProjectPathId,
    ProjectRootPath,
    contains_path,
    join_path,
    resolve_project_root,
)

from vcsinstall.domain import ContentHash, ContentKind, Dependency, DependencyName, SourcePath
from vcsinstall.filesystem import FSClient
from vcsinstall.runtime import errors
from vcsinstall.runtime.cells import sync_cells
from vcsinstall.runtime.commands import _prepare_dependency_lock, _validate_destination_parents
from vcsinstall.runtime.entities import LocalContentPolicy, LocalStatus, SyncEntry, SyncPlan
from vcsinstall.runtime.validation import local_content_status, validate_no_local_content_conflicts
from vcsinstall.sources import get_adapter
from vcsinstall.workspace import Workspace, active_workspace

_ADMIN_NAMES = frozenset({".git"})


def _scoped_files(entry: SyncEntry, root: SourcePath) -> dict[ProjectPathId, ContentHash]:
    files = {join_path(entry.source_path, path): digest for path, digest in entry.content.file_hashes.items()}
    return {path: digest for path, digest in files.items() if contains_path(root, path)}


def validate_sync_mappings(  # noqa: CCR001 — Compare each overlapping pair at its shared scope.
    entries: tuple[SyncEntry, ...],
) -> Result[None]:
    """Require overlapping mappings to propose identical complete content.

    Two '@/docs' mappings with different guide hashes fail. A nested '@/docs/a'
    mapping passes only when it agrees with its enclosing directory's a content.
    """
    for index, entry in enumerate(entries):
        for other in entries[:index]:
            if contains_path(entry.source_path, other.source_path):
                parent, child = entry, other
            elif contains_path(other.source_path, entry.source_path):
                parent, child = other, entry
            else:
                continue
            obstruction = (
                parent.source_path != child.source_path
                and parent.content.kind == ContentKind.file
                and bool(child.content.files)
            )
            if obstruction or _scoped_files(parent, child.source_path) != _scoped_files(child, child.source_path):
                return Err(
                    [
                        InvalidArguments(
                            reason=f"Conflicting source mappings at {parent.source_path} and {child.source_path}."
                        )
                    ]
                )
    return Ok(None)


def _covering_entries(entries: tuple[SyncEntry, ...]) -> tuple[SyncEntry, ...]:
    selected: list[SyncEntry] = []
    for entry in sorted(entries, key=lambda item: (len(item.source_path), item.source_path, item.destination)):
        if not any(contains_path(parent.source_path, entry.source_path) for parent in selected):
            selected.append(entry)
    return tuple(selected)


def _protected_paths(workspace: Workspace) -> tuple[Path, ...]:
    paths: tuple[Path, ...] = (
        workspace.config_path,
        workspace.lockfile_path,
        *(workspace.project_root / entry.destination.removeprefix("@/") for entry in workspace.config.entries),
    )
    if runtime_config.is_set():
        paths += (runtime_config.get().cache_dir,)
    return paths


@unwrap_to_error
def _validate_sync_target(  # noqa: CCR001 — Check independent preservation and containment constraints.
    entry: SyncEntry, *, recipient: FSClient, protected: tuple[Path, ...]
) -> Result[None]:
    """Protect source administration and project state.

    A directory mapping may replace its files while retaining '.git'. Replacing
    an ancestor of preserved metadata by a regular file fails, including with force.
    """
    proposed = tuple(join_path(entry.source_path, file.path) for file in entry.content.files)
    for path in (ProjectPathId(entry.source_path), *proposed):
        if any(part.casefold() in _ADMIN_NAMES for part in path.removeprefix("@/").split("/")):
            return Err([InvalidArguments(reason=f"Synchronization cannot write repository administration at {path}.")])
    target = recipient.resolve_path(entry.source_path).unwrap()
    if any(target == path or target.is_relative_to(path) or path.is_relative_to(target) for path in protected):
        return Err(
            [
                InvalidArguments(
                    reason=f"Source mapping {entry.source_path} overlaps project state or managed cache data."
                )
            ]
        )
    if entry.source_path == "@/" and entry.content.kind == ContentKind.file:
        return Err([InvalidArguments(reason="The source directory root must remain a directory.")])
    for excluded in entry.recipient.excluded_paths:
        if any(contains_path(file.path, excluded) for file in entry.content.files):
            return Err(
                [
                    InvalidArguments(
                        reason=f"Source mapping {entry.source_path} would replace preserved repository administration."
                    )
                ]
            )
    return Ok(None)


@unwrap_to_error
def _prepare_sync_plan(  # noqa: CCR001 — Prepare all selected mappings before permitting effects.
    name: DependencyName, source_dir: ProjectRootPath, *, policy: LocalContentPolicy
) -> Result[SyncPlan | None]:
    workspace = active_workspace.get()
    declared = workspace.config.dependencies.get(name)
    if declared is None:
        return Err([InvalidArguments(reason=f"Unknown dependency {name!r}.")])
    if any(part.casefold() in _ADMIN_NAMES for part in source_dir.parts):
        return Err([InvalidArguments(reason="SOURCE_DIR must not be inside repository administration.")])
    recipient = FSClient(root=source_dir)
    if recipient.inspect_kind().unwrap() != ContentKind.directory:
        return Err([InvalidArguments(reason=f"SOURCE_DIR must be an existing directory: {source_dir}")])
    entries = sorted(workspace.config.entries_for_dependency(name), key=lambda entry: entry.destination)
    if not entries:
        return Ok(None)
    locked = _prepare_dependency_lock(workspace, name, source=get_adapter(declared.vcs)).unwrap()
    local = FSClient(root=workspace.project_root)
    changes: list[SyncEntry] = []
    for declared_entry in entries:
        baseline = workspace.require_matching_entry_baseline(declared_entry).unwrap().file_hashes()
        _validate_destination_parents(declared_entry.destination, filesystem=local).unwrap()
        content = local.inspect_tree(declared_entry.destination).unwrap()
        status = local_content_status(baseline, files=content.file_hashes)
        if status == LocalStatus.unmodified:
            continue
        if any(part.casefold() in _ADMIN_NAMES for part in declared_entry.source_path.removeprefix("@/").split("/")):
            return Err(
                [
                    InvalidArguments(
                        reason="Synchronization cannot target repository administration "
                        f"at {declared_entry.source_path}."
                    )
                ]
            )
        changes.append(
            SyncEntry(
                source_path=declared_entry.source_path,
                destination=declared_entry.destination,
                content=content,
                baseline=baseline,
                status=status,
                recipient=recipient.inspect_tree(declared_entry.source_path, exclude_names=_ADMIN_NAMES).unwrap(),
            )
        )
    selected = tuple(changes)
    validate_sync_mappings(selected).unwrap()
    protected = _protected_paths(workspace) if selected else ()
    for entry in selected:
        _validate_sync_target(entry, recipient=recipient, protected=protected).unwrap()
        validate_no_local_content_conflicts(
            entry.baseline,
            entry.content.file_hashes,
            files=entry.recipient.file_hashes,
            destination=entry.source_path,
            policy=policy,
        ).unwrap()
    for entry in _covering_entries(selected):
        _validate_destination_parents(entry.source_path, filesystem=recipient).unwrap()
    return Ok(
        SyncPlan(
            name=name,
            definition=Dependency.model_validate(locked.model_dump(exclude={"revision"})),
            revision=locked.revision,
            source_dir=source_dir,
            entries=selected,
        )
    )


@unwrap_to_error
def _apply_sync_entry(entry: SyncEntry, *, name: DependencyName, recipient: FSClient, local: FSClient) -> Result[None]:
    preserved = frozenset(join_path(entry.source_path, path) for path in entry.recipient.excluded_paths)
    recipient.remove(entry.source_path, preserve_root=entry.source_path == "@/", preserve_paths=preserved).unwrap()
    for file in entry.content.files:
        recipient.write_stream(
            join_path(entry.source_path, file.path),
            partial(local.copy_file, join_path(entry.destination, file.path)),
            executable=file.executable,
        ).unwrap()
    written = recipient.inspect_tree(entry.source_path, exclude_names=_ADMIN_NAMES).unwrap()
    if written.file_hashes != entry.content.file_hashes:
        return Err(
            [
                errors.SyncFailed(
                    dependency=name,
                    source_dir=recipient.root,
                    reason=f"Written content at {entry.source_path} failed verification.",
                )
            ]
        )
    return Ok(None)


@unwrap_to_error
def dependency_sync_to(name: DependencyName, source_dir: PathInput, *, force: bool = False) -> Result[None]:
    """Mirror changed entries into a local root without acknowledging installation changes."""
    root = resolve_project_root(source_dir).unwrap()
    policy = LocalContentPolicy.overwrite if force else LocalContentPolicy.protect
    plan = _prepare_sync_plan(name, root, policy=policy).unwrap()
    if plan is None:
        return Ok(None)
    local = FSClient(root=active_workspace.get().project_root)
    recipient = FSClient(root=root, readonly=False)
    for entry in _covering_entries(plan.entries):
        if not entry.changed:
            continue
        result = _apply_sync_entry(entry, name=name, recipient=recipient, local=local)
        if result.is_err():
            return Err([errors.SyncWriteFailed(source_dir=root), *result.unwrap_err()])
    output.get().emit(sync_cells(plan)).unwrap()
    return Ok(None)
