"""Guards for entry destinations and loaded configuration and lockfile state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import ProjectPathId, ResolvedProjectPath, project_path_id_from_resolved

from vcsinstall.domain import DependencyName
from vcsinstall.sources import SourceAdapter
from vcsinstall.workspace import errors
from vcsinstall.workspace.config import Entry
from vcsinstall.workspace.lockfile import DependencyLock, InstallationBaseline

if TYPE_CHECKING:
    from vcsinstall.workspace import Workspace


def validate_destinations(paths: tuple[ProjectPathId, ...], *, reserved: tuple[ProjectPathId, ...]) -> Result[None]:
    """Reject root, duplicate, overlapping, and reserved destination identifiers.

    Example: '@/docs' and '@/docs/guide' conflict; '@/docs' and '@/licenses'
    pass when neither overlaps a reserved project file. No files are inspected.
    """
    seen: list[ProjectPathId] = []
    for path in paths:
        if path == "@/" or any(
            path == other or path.startswith(other + "/") or other.startswith(path + "/")
            for other in (*reserved, *seen)
        ):
            return Err([InvalidArguments(reason=f"Destination {path!r} overlaps another entry or a project file.")])
        seen.append(path)
    return Ok(None)


def validate_no_missing_baselines(workspace: Workspace, dependency: DependencyName) -> Result[None]:
    """Require each declared destination to have a baseline for this dependency.

    Example: ci declaring '@/docs' fails if the lockfile only records ci at
    '@/licenses', or records '@/docs' for a different dependency.
    """
    destinations = (
        {entry.destination for entry in workspace.lockfile.entries_for_dependency(dependency)}
        if workspace.lockfile
        else set()
    )
    for entry in workspace.config.entries_for_dependency(dependency):
        if entry.destination not in destinations:
            return Err(
                [
                    errors.MissingBaseline(
                        path=workspace.lockfile_path,
                        dependency=dependency,
                        destination=entry.destination,
                    )
                ]
            )
    return Ok(None)


def validate_no_orphaned_baselines(workspace: Workspace, dependency: DependencyName) -> Result[None]:
    """Require each recorded destination to remain declared for this dependency.

    Example: a recorded ci baseline at '@/old-docs' fails after that ci entry
    is removed or reassigned to another dependency in configuration.
    """
    if workspace.lockfile is None:
        return Ok(None)
    destinations = {entry.destination for entry in workspace.config.entries_for_dependency(dependency)}
    for baseline in workspace.lockfile.entries_for_dependency(dependency):
        if baseline.destination not in destinations:
            return Err(
                [
                    errors.OrphanedBaseline(
                        path=workspace.lockfile_path,
                        dependency=dependency,
                        destination=baseline.destination,
                    )
                ]
            )
    return Ok(None)


def validate_no_reassigned_baselines(workspace: Workspace, dependency: DependencyName) -> Result[None]:
    """Keep prior baselines protecting entries reassigned to another dependency.

    Example: an old ci baseline at '@/docs' blocks discarding ci's records
    while formatting declares '@/docs'. An undeclared old destination passes.
    """
    if workspace.lockfile is None:
        return Ok(None)
    for baseline in workspace.lockfile.entries_for_dependency(dependency):
        entry = workspace.config.entry_for_destination(baseline.destination)
        if entry is not None and entry.dependency != dependency:
            return Err(
                [
                    errors.BaselineMismatch(
                        path=workspace.lockfile_path,
                        dependency=entry.dependency,
                        destination=entry.destination,
                    )
                ]
            )
    return Ok(None)


def require_matching_dependency_lock(
    workspace: Workspace, dependency: DependencyName, *, source: SourceAdapter
) -> Result[DependencyLock]:
    """Require matching backend, prepared repository, and source-owned selector rules.

    Example: ci configured for Git branch 'main' cannot reuse a lock for tag
    'main' or another repository. A matching record is returned unchanged;
    this check does not resolve repositories or test upstream freshness.
    """
    declared = workspace.config.dependencies.get(dependency)
    if declared is None:
        return Err([InvalidArguments(reason=f"Unknown dependency {dependency!r}.")])
    recorded = workspace.lockfile.dependencies.get(dependency) if workspace.lockfile else None
    problem = errors.DependencyLockRequired(path=workspace.lockfile_path, dependency=dependency)
    if (
        recorded is None
        or declared.vcs != recorded.vcs
        or declared.repository != recorded.repository
        or not source.selectors_match(declared.selector, recorded.selector)
    ):
        return Err([problem])
    return Ok(recorded)


def _reserved_destinations(workspace: Workspace) -> tuple[ProjectPathId, ...]:
    return tuple(
        project_path_id_from_resolved(ResolvedProjectPath(path), workspace.project_root)
        for path in (workspace.config_path, workspace.lockfile_path)
    )


@unwrap_to_error
def validate_workspace_destinations(workspace: Workspace) -> Result[None]:
    """Check each persisted destination collection independently, without filesystem access.

    Example: configuration at '@/new' and an old baseline at '@/old' can pass;
    two configuration entries at '@/new' fail. Matching declarations to
    baselines is a separate check.
    """
    reserved = _reserved_destinations(workspace)
    baselines = workspace.lockfile.entries if workspace.lockfile else ()
    for collection in (workspace.config.entries, baselines):
        validate_destinations(tuple(item.destination for item in collection), reserved=reserved).unwrap()
    return Ok(None)


@unwrap_to_error
def validate_destination_ownership(workspace: Workspace, destination: ProjectPathId) -> Result[None]:
    """Check one destination against declared and recorded ownership, allowing an exact match.

    Example: existing '@/docs/shared' blocks proposed '@/docs/shared/guide'.
    Proposing '@/docs/shared' itself passes this guard; runtime separately
    checks replacement permission. Only canonical identifiers are compared.
    """
    baselines = workspace.lockfile.entries if workspace.lockfile else ()
    destinations = {item.destination for item in (*workspace.config.entries, *baselines)}
    destinations.discard(destination)
    validate_destinations((destination,), reserved=_reserved_destinations(workspace)).unwrap()
    for existing in sorted(destinations):
        validate_destinations((existing, destination), reserved=()).unwrap()
    return Ok(None)


def require_entry_baseline(workspace: Workspace, entry: Entry) -> Result[InstallationBaseline]:
    """Return the recorded baseline at this entry's destination, failing if none exists.

    Example: replacing an entry at '@/docs' retrieves the old baseline there,
    even if its dependency or source path differs from the proposed mapping.
    No baseline at '@/docs' produces MissingBaseline, without inspecting files.
    """
    previous = workspace.lockfile.baseline_for_destination(entry.destination) if workspace.lockfile else None
    if previous is None:
        return Err(
            [
                errors.MissingBaseline(
                    path=workspace.lockfile_path,
                    dependency=entry.dependency,
                    destination=entry.destination,
                )
            ]
        )
    return Ok(previous)


@unwrap_to_error
def require_matching_entry_baseline(workspace: Workspace, entry: Entry) -> Result[InstallationBaseline]:
    """Return a destination's baseline only when its dependency and source path agree.

    Example: a ci '@/guide' entry cannot use a ci '@/old-guide' baseline at
    the same destination. Dependency-lock matching is checked separately.
    """
    baseline = require_entry_baseline(workspace, entry).unwrap()
    if baseline.dependency != entry.dependency or baseline.source_path != entry.source_path:
        return Err(
            [
                errors.BaselineMismatch(
                    path=workspace.lockfile_path,
                    dependency=entry.dependency,
                    destination=entry.destination,
                )
            ]
        )
    return Ok(baseline)
