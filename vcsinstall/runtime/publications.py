"""Selection and baseline verification for publishing one dependency's local changes."""

from pathlib import Path

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config.runtime import runtime_config
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.output import output
from llm_tool_cli.paths import PathInput, ProjectPathId, ResolvedProjectPath, resolve_project_root

from vcsinstall.domain import ContentKind, Dependency, DependencyName
from vcsinstall.filesystem import FSClient
from vcsinstall.filesystem import errors as filesystem_errors
from vcsinstall.hosting import (
    Contribution,
    HostingAdapter,
    PublicationRequest,
    PushOptions,
    validate_mappings,
)
from vcsinstall.runtime.cells import push_cells
from vcsinstall.runtime.commands import _prepare_dependency_lock, _validate_destination_parents
from vcsinstall.runtime.entities import PushEntry, PushPlan
from vcsinstall.runtime.validation import local_content_status
from vcsinstall.sources import SourceAdapter
from vcsinstall.workspace import Entry, Workspace, active_workspace


@unwrap_to_error
def _protected_paths(workspace: Workspace) -> Result[tuple[Path, ...]]:
    filesystem = FSClient(root=workspace.project_root)
    return Ok(
        (
            workspace.config_path,
            workspace.lockfile_path,
            *(filesystem.resolve_path(entry.destination).unwrap() for entry in workspace.config.entries),
        )
    )


@unwrap_to_error
def _prepare_patch_path(path: PathInput | None, workspace: Workspace) -> Result[ResolvedProjectPath | None]:
    if path is None:
        return Ok(None)
    parent = resolve_project_root(path.parent).unwrap()
    filesystem = FSClient(root=parent)
    identifier = ProjectPathId("@/" + path.name)
    if filesystem.inspect_kind(identifier).unwrap() not in (ContentKind.absent, ContentKind.file):
        return Err(
            [filesystem_errors.AccessFailed(path=parent / path.name, reason="Artifact target must be a regular file.")]
        )
    resolved = filesystem.resolve_path(identifier).unwrap()
    reserved = (*_protected_paths(workspace).unwrap(), runtime_config.get().cache_dir)
    if any(
        resolved == other or resolved.is_relative_to(other) or other.is_relative_to(resolved) for other in reserved
    ):
        return Err(
            [
                InvalidArguments(
                    reason="Patch output overlaps project records, an installation destination, or managed cache data."
                )
            ]
        )
    return Ok(resolved)


@unwrap_to_error
def _select_push_entries(
    workspace: Workspace, name: DependencyName, destinations: tuple[ProjectPathId, ...]
) -> Result[tuple[Entry, ...]]:
    """Select this dependency's exact entry roots, preserving deterministic destination order."""
    if name not in workspace.config.dependencies:
        return Err([InvalidArguments(reason=f"Unknown dependency {name!r}.")])
    if not destinations:
        return Ok(tuple(sorted(workspace.config.entries_for_dependency(name), key=lambda entry: entry.destination)))

    entries = []
    for destination in sorted(set(destinations)):
        entry = workspace.config.entry_for_destination(destination)
        if entry is None or entry.dependency != name:
            return Err([InvalidArguments(reason=f"Entry {destination!r} is not declared for dependency {name!r}.")])
        entries.append(entry)
    return Ok(tuple(entries))


@unwrap_to_error
def _inspect_push_entries(workspace: Workspace, entries: tuple[Entry, ...]) -> Result[tuple[PushEntry, ...]]:
    """Inspect complete local mappings only after requiring matching installation baselines."""
    filesystem = FSClient(root=workspace.project_root)
    inspected = []
    for entry in entries:
        baseline = workspace.require_matching_entry_baseline(entry).unwrap().file_hashes()
        _validate_destination_parents(entry.destination, filesystem=filesystem).unwrap()
        content = filesystem.inspect_tree(entry.destination).unwrap()
        inspected.append(
            PushEntry(
                contribution=Contribution(
                    source_path=entry.source_path,
                    destination=entry.destination,
                    content=content,
                    baseline=baseline,
                ),
                status=local_content_status(baseline, files=content.file_hashes),
            )
        )
    return Ok(tuple(inspected))


@unwrap_to_error
def _prepare_push_plan(
    name: DependencyName,
    destinations: tuple[ProjectPathId, ...] = (),
    *,
    options: PushOptions,
    source: SourceAdapter,
    hosting: HostingAdapter,
) -> Result[PushPlan | None]:
    """Prepare selection, artifacts, and verified local changes before any publication."""
    workspace = active_workspace.get()
    entries = _select_push_entries(workspace, name, destinations).unwrap()
    declared = workspace.config.dependencies[name]
    hosting.validate_options(declared, options).unwrap()
    patch_file = _prepare_patch_path(options.patch_file, workspace).unwrap()
    if patch_file is not None:
        repository = source.normalize_repository(declared.repository, cwd=workspace.project_root).unwrap()
        hosting.validate_artifact(declared.replace(repository=repository or declared.repository), patch_file).unwrap()
    if not entries:
        return Ok(None)

    locked = _prepare_dependency_lock(workspace, name, source=source).unwrap()
    definition = Dependency.model_validate(locked.model_dump(exclude={"revision"}))
    hosting.validate_options(definition, options).unwrap()
    inspected = _inspect_push_entries(workspace, entries).unwrap()
    changes = tuple(entry.contribution for entry in inspected if entry.changed)
    validate_mappings(changes).unwrap()
    request = None
    if changes:
        request = PublicationRequest(
            dependency=definition,
            revision=locked.revision,
            local_root=workspace.project_root,
            protected_paths=_protected_paths(workspace).unwrap(),
            changes=changes,
            options=options,
            patch_file=patch_file,
        )
    return Ok(
        PushPlan(
            name=name,
            definition=definition,
            revision=locked.revision,
            options=options,
            entries=tuple(entry for entry in inspected if destinations or entry.changed),
            request=request,
        )
    )


@unwrap_to_error
def dependency_push(
    name: DependencyName,
    destinations: tuple[ProjectPathId, ...] = (),
    *,
    options: PushOptions,
    source: SourceAdapter,
    hosting: HostingAdapter,
) -> Result[None]:
    """Publish selected complete local trees without acknowledging them as installation baselines."""
    plan = _prepare_push_plan(name, destinations, options=options, source=source, hosting=hosting).unwrap()
    if plan is None:
        return Ok(None)

    delivery = None
    if plan.request is not None:
        delivery = hosting.publish(plan.request, source=source).unwrap()

    output.get().emit(push_cells(plan, delivery)).unwrap()
    return Ok(None)
