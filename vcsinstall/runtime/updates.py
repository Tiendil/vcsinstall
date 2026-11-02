"""Preparation and application of updates spanning independently locked dependencies."""

from collections.abc import Callable
from contextlib import ExitStack
from functools import partial

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.errors import EnvironmentErrors
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.output import output

from vcsinstall.domain import Dependency, DependencyName, VcsId
from vcsinstall.filesystem import FSClient
from vcsinstall.hosting import HostingAdapter
from vcsinstall.runtime import errors
from vcsinstall.runtime.cells import dependency_update_cells
from vcsinstall.runtime.commands import _apply_entry_changes, _prepare_dependency_lock, _prepare_dependency_plan
from vcsinstall.runtime.entities import DependencyPlan
from vcsinstall.sources import SourceAdapter
from vcsinstall.workspace import (
    Lockfile,
    Workspace,
    active_workspace,
)
from vcsinstall.workspace import errors as workspace_errors
from vcsinstall.workspace import serialize_workspace_changes, write_workspace_files


def _update_errors(name: DependencyName, problems: EnvironmentErrors) -> EnvironmentErrors:
    """Add dependency context without escalating shared invalid-argument failures."""
    if all(isinstance(problem, InvalidArguments) for problem in problems):
        return [InvalidArguments(reason=f"Cannot update dependency {name!r}."), *problems]
    return [errors.DependencyUpdateFailed(dependency=name), *problems]


@unwrap_to_error
def _prepare_update_definition(
    workspace: Workspace, name: DependencyName, *, source: SourceAdapter
) -> Result[Dependency]:
    workspace.validate_no_orphaned_baselines(name).unwrap()
    workspace.validate_no_missing_baselines(name).unwrap()
    filesystem = FSClient(root=workspace.project_root)
    for entry in workspace.config.entries_for_dependency(name):
        filesystem.resolve_path(entry.destination).unwrap()

    matching = _prepare_dependency_lock(workspace, name, source=source)
    if not matching.is_err(workspace_errors.DependencyLockRequired):
        definition = Dependency.model_validate(matching.unwrap().model_dump(exclude={"revision"}))
    else:
        declared = workspace.config.dependencies[name]
        repository = source.normalize_repository(declared.repository, cwd=workspace.project_root).unwrap()
        if repository is None:
            return Err([InvalidArguments(reason=f"Unsupported repository locator for dependency {name!r}.")])
        definition = declared.replace(repository=repository)
    source.validate_input(definition).unwrap()
    return Ok(definition)


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
    definitions = {
        name: (
            _prepare_update_definition(workspace, name, source=sources[name])
            .map_err(partial(_update_errors, name))
            .unwrap()
        )
        for name in selected
    }
    filesystem = FSClient(root=workspace.project_root, readonly=False)
    plans: list[DependencyPlan] = []
    lockfile = workspace.lockfile or Lockfile(version=1)
    with ExitStack() as acquired_sources:
        for name, definition in definitions.items():
            hosting = hosting_for(definition.vcs).map_err(partial(_update_errors, name)).unwrap()
            acquired = acquired_sources.enter_context(hosting.acquire(definition))
            path = acquired.map_err(partial(_update_errors, name)).unwrap()
            source = sources[name]
            resolved = source.resolve(definition, path).map_err(partial(_update_errors, name)).unwrap()
            plan = (
                _prepare_dependency_plan(workspace, name, resolved, source=source)
                .map_err(partial(_update_errors, name))
                .unwrap()
            )
            plans.append(plan)
            lockfile = lockfile.with_dependency(name, plan.lock).with_baselines(
                entry.baseline for entry in plan.entries
            )

        serialized = serialize_workspace_changes(workspace, workspace.config, lockfile)
        for plan in plans:
            _apply_entry_changes(
                plan.entries, filesystem=filesystem, copy_file=partial(sources[plan.name].copy_file, plan.resolved)
            ).map_err(partial(_update_errors, plan.name)).unwrap()
        write_workspace_files(workspace, serialized).unwrap()
    output.get().emit(dependency_update_cells(plans)).unwrap()
    return Ok(None)
