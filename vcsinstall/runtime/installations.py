"""Whole-project installation using matching locks and complete local-content protection."""

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
    _plan_entry,
    _prepare_dependency_plan,
    _prepare_dependency_request,
)
from vcsinstall.runtime.entities import DependencyPlan, DependencyRequest
from vcsinstall.runtime.validation import validate_no_local_content_conflicts
from vcsinstall.sources import ResolvedSource, SourceAdapter
from vcsinstall.workspace import Lockfile, Workspace, active_workspace
from vcsinstall.workspace import errors as workspace_errors
from vcsinstall.workspace import serialize_workspace_changes, write_workspace_files


def _installation_errors(name: DependencyName, problems: EnvironmentErrors) -> EnvironmentErrors:
    """Attach dependency context while preserving shared invalid-argument classification."""
    if all(isinstance(problem, InvalidArguments) for problem in problems):
        return [InvalidArguments(reason=f"Cannot install dependency {name!r}."), *problems]
    return [errors.DependencyInstallationFailed(dependency=name), *problems]


@unwrap_to_error
def _prepare_installation_request(  # noqa: CCR001 — Keep lock matching and reuse of observed local content together.
    workspace: Workspace, name: DependencyName, *, source: SourceAdapter, locked: bool
) -> Result[DependencyRequest]:
    request = _prepare_dependency_request(workspace, name, source=source).unwrap()
    if locked and request.lock is None:
        return Err([workspace_errors.DependencyLockRequired(path=workspace.lockfile_path, dependency=name)])

    filesystem = FSClient(root=workspace.project_root)
    observed = {}
    retained = []
    for entry in workspace.config.entries_for_dependency(name):
        tree = filesystem.inspect_tree(entry.destination).unwrap()
        observed[entry.destination] = tree
        if request.lock is None:
            continue
        matching_baseline = workspace.require_matching_entry_baseline(entry)
        if matching_baseline.is_err(workspace_errors.MissingBaseline) or matching_baseline.is_err(
            workspace_errors.BaselineMismatch
        ):
            continue
        baseline = matching_baseline.unwrap()
        expected = baseline.file_hashes()
        validate_no_local_content_conflicts(
            expected, expected, files=tree.file_hashes, destination=entry.destination
        ).unwrap()
        if expected == tree.file_hashes:
            retained.append(
                _plan_entry(entry, source=tree, previous=baseline, filesystem=filesystem, observed=tree).unwrap()
            )
    return Ok(request.replace(observed=observed, retained=retained))


def _installation_lockfile(workspace: Workspace, plans: list[DependencyPlan]) -> Lockfile:
    """Reconcile recorded membership after preparation, retaining existing record order."""
    lockfile = _lockfile_with_plans(workspace, plans)
    for name in lockfile.dependencies.keys() - workspace.config.dependencies.keys():
        lockfile = lockfile.without_dependency(name)
    destinations = {entry.destination for entry in workspace.config.entries}
    return lockfile.without_entries(
        tuple(baseline.destination for baseline in lockfile.entries if baseline.destination not in destinations)
    )


@unwrap_to_error
def install(  # noqa: CCR001 — Keep whole-project preparation, acquisition lifetimes, and effects in one scope.
    *,
    source_for: Callable[[VcsId], SourceAdapter],
    hosting_for: Callable[[VcsId], Result[HostingAdapter]],
    locked: bool = False,
) -> Result[None]:
    """Install the complete declared project, preparing every dependency before project mutation."""
    workspace = active_workspace.get()
    if locked:
        workspace.validate_complete_records().unwrap()
    sources = {name: source_for(dependency.vcs) for name, dependency in sorted(workspace.config.dependencies.items())}
    requests = [
        _prepare_installation_request(workspace, name, source=source, locked=locked)
        .map_err(partial(_installation_errors, name))
        .unwrap()
        for name, source in sources.items()
    ]

    plans = []
    with ExitStack() as acquired_sources:
        for request in requests:
            name = request.name
            source = sources[name]
            resolved = None
            if request.lock is None or len(request.retained) != len(request.observed):
                hosting = hosting_for(request.definition.vcs).map_err(partial(_installation_errors, name)).unwrap()
                acquired = acquired_sources.enter_context(
                    hosting.acquire(
                        request.definition,
                        revision=request.lock.revision if request.lock else None,
                        refresh=request.lock is None,
                    )
                )
                path = acquired.map_err(partial(_installation_errors, name)).unwrap()
                if request.lock is not None:
                    resolved = ResolvedSource(dependency=request.definition, path=path, revision=request.lock.revision)
                else:
                    resolved = (
                        source.resolve(request.definition, path).map_err(partial(_installation_errors, name)).unwrap()
                    )
            plans.append(
                _prepare_dependency_plan(workspace, request, resolved, source=source, allow_absent=False)
                .map_err(partial(_installation_errors, name))
                .unwrap()
            )

        lockfile = workspace.lockfile if locked else _installation_lockfile(workspace, plans)
        serialized = serialize_workspace_changes(workspace, workspace.config, lockfile)
        filesystem = FSClient(root=workspace.project_root, readonly=False)
        for plan in plans:
            _apply_dependency_plan(plan, filesystem=filesystem, source=sources[plan.name]).map_err(
                partial(_installation_errors, plan.name)
            ).unwrap()
        write_workspace_files(workspace, serialized).unwrap()
    output.get().emit(dependency_change_cells(plans, operation=Operation.install)).unwrap()
    return Ok(None)
