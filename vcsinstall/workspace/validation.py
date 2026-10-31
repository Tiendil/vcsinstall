"""Guards for entry destinations and loaded configuration and lockfile state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import DependencyName
from vcsinstall.workspace import errors

if TYPE_CHECKING:
    from vcsinstall.workspace import Workspace


def validate_destinations(paths: tuple[ProjectPathId, ...], *, reserved: tuple[ProjectPathId, ...]) -> Result[None]:
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
    """Require each declared destination to have a baseline for this dependency."""
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
    """Require each recorded destination to remain declared for this dependency."""
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
