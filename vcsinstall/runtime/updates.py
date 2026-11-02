"""Preparation and application of updates spanning independently locked dependencies."""

from collections.abc import Callable
from contextlib import ExitStack
from functools import partial

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.errors import EnvironmentErrors
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.output import output

from vcsinstall.domain import DependencyName, VcsId
from vcsinstall.filesystem import FSClient
from vcsinstall.hosting import HostingAdapter
from vcsinstall.runtime import errors
from vcsinstall.runtime.cells import Operation, dependency_change_cells
from vcsinstall.runtime.commands import (
    _apply_dependency_plan,
    _lockfile_with_plans,
    _prepare_dependency_plan,
    _prepare_dependency_request,
)
from vcsinstall.runtime.entities import DependencyPlan, DependencyRequest
from vcsinstall.sources import SourceAdapter
from vcsinstall.workspace import (
    Workspace,
    active_workspace,
    serialize_workspace_changes,
    write_workspace_files,
)


def _update_errors(name: DependencyName, problems: EnvironmentErrors) -> EnvironmentErrors:
    """Add dependency context without escalating shared invalid-argument failures."""
    if all(isinstance(problem, InvalidArguments) for problem in problems):
        return [InvalidArguments(reason=f"Cannot update dependency {name!r}."), *problems]
    return [errors.DependencyUpdateFailed(dependency=name), *problems]


@unwrap_to_error
def _prepare_update_request(
    workspace: Workspace, name: DependencyName, *, source: SourceAdapter
) -> Result[DependencyRequest]:
    workspace.validate_no_orphaned_baselines(name).unwrap()
    workspace.validate_no_missing_baselines(name).unwrap()
    filesystem = FSClient(root=workspace.project_root)
    for entry in workspace.config.entries_for_dependency(name):
        filesystem.resolve_path(entry.destination).unwrap()

    return _prepare_dependency_request(workspace, name, source=source)


@unwrap_to_error
def dependency_update(
    names: tuple[DependencyName, ...] = (),
    *,
    source_for: Callable[[VcsId], SourceAdapter],
    hosting_for: Callable[[VcsId], Result[HostingAdapter]],
) -> Result[None]:
    """Refresh all selected dependencies, preparing every entry before any project write."""
    workspace = active_workspace.get()
    selected = sorted(set(names) if names else workspace.config.dependencies)
    unknown = [name for name in selected if name not in workspace.config.dependencies]
    if unknown:
        return Err([InvalidArguments(reason=f"Unknown dependency {name!r}.") for name in unknown])
    if not selected:
        return Ok(None)

    sources = {name: source_for(workspace.config.dependencies[name].vcs) for name in selected}
    requests = {
        name: (
            _prepare_update_request(workspace, name, source=sources[name])
            .map_err(partial(_update_errors, name))
            .unwrap()
        )
        for name in selected
    }
    filesystem = FSClient(root=workspace.project_root, readonly=False)
    plans: list[DependencyPlan] = []
    with ExitStack() as acquired_sources:
        for name, request in requests.items():
            definition = request.definition
            hosting = hosting_for(definition.vcs).map_err(partial(_update_errors, name)).unwrap()
            acquired = acquired_sources.enter_context(hosting.acquire(definition))
            path = acquired.map_err(partial(_update_errors, name)).unwrap()
            source = sources[name]
            resolved = source.resolve(definition, path).map_err(partial(_update_errors, name)).unwrap()
            plan = (
                _prepare_dependency_plan(workspace, request, resolved, source=source)
                .map_err(partial(_update_errors, name))
                .unwrap()
            )
            plans.append(plan)

        lockfile = _lockfile_with_plans(workspace, plans)
        serialized = serialize_workspace_changes(workspace, workspace.config, lockfile)
        for plan in plans:
            _apply_dependency_plan(plan, filesystem=filesystem, source=sources[plan.name]).map_err(
                partial(_update_errors, plan.name)
            ).unwrap()
        write_workspace_files(workspace, serialized).unwrap()
    output.get().emit(dependency_change_cells(plans, operation=Operation.dependency_update)).unwrap()
    return Ok(None)
