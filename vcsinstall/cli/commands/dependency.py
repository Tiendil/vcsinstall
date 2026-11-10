from pathlib import Path

import pydantic
import typer
from llm_tool_cli.cli.context import command_context
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import PathInput

from vcsinstall.cli.commands.push import push_dependency
from vcsinstall.cli.entities import (
    BranchOption,
    DependencyNameArgument,
    DependencyNamesArgument,
    ForceOverwriteOption,
    RedefineOption,
    RepositoryArgument,
    RevisionOption,
    TagOption,
    VcsOption,
)
from vcsinstall.domain import Dependency, RepositoryLocator, SelectorKind, VcsId, VersionSelector, urls
from vcsinstall.hosting import GitHostingAdapter, HostingAdapter, get_hosting
from vcsinstall.runtime import (
    dependency_add,
    dependency_list,
    dependency_outdated,
    dependency_remove,
    dependency_update,
)
from vcsinstall.sources import (
    DEFAULT_ADAPTERS,
    DETECTION_ORDER,
    AdapterRegistry,
    SourceRequest,
    get_adapter,
    parse_repository,
)
from vcsinstall.workspace import open_workspace

app = typer.Typer(
    help="Manage named dependencies.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.command("push")(push_dependency)


@unwrap_to_error
def _detect_backend(  # noqa: CCR001 — Ordered source checks and hosting-owned remote probes stay together.
    repository: RepositoryLocator,
    *,
    cwd: Path,
    vcs: VcsId | None,
    adapters: AdapterRegistry,
    hosting: HostingAdapter | None,
) -> Result[VcsId]:
    if vcs is not None:
        return Ok(vcs)
    for candidate in DETECTION_ORDER:
        adapter = get_adapter(candidate, adapters=adapters)
        if adapter.probe_repository(repository, cwd=cwd).unwrap():
            return Ok(candidate)
        if candidate != VcsId.git or not urls.is_url(repository):
            continue
        native = adapter.normalize_repository(repository, cwd=cwd).unwrap()
        if native is not None and urls.is_url(native):
            probe = (
                hosting.probe_repository(native) if hosting is not None else GitHostingAdapter.probe_repository(native)
            )
            if probe.unwrap():
                return Ok(candidate)
    return Err(
        [
            InvalidArguments(
                reason=f"No source backend recognized repository {repository!r}.",
                ways_to_fix=["Select a backend with --vcs or provide a supported repository locator."],
            )
        ]
    )


@unwrap_to_error
def prepare_dependency(
    request: SourceRequest,
    *,
    cwd: Path,
    vcs: VcsId | None = None,
    adapters: AdapterRegistry = DEFAULT_ADAPTERS,
    hosting: HostingAdapter | None = None,
) -> Result[Dependency]:
    """Select a source backend and normalize invocation-relative repository input."""
    selected = _detect_backend(request.repository, cwd=cwd, vcs=vcs, adapters=adapters, hosting=hosting).unwrap()
    source = get_adapter(selected, adapters=adapters)
    if selected == VcsId.noop and adapters is DEFAULT_ADAPTERS:
        return Err([InvalidArguments(reason="The noop backend is available only to test environments.")])
    locator = source.normalize_repository(request.repository, cwd=cwd).unwrap()
    if locator is None:
        return Err(
            [
                InvalidArguments(
                    reason=f"Repository {request.repository!r} is not supported by the selected {selected} backend.",
                    ways_to_fix=["Provide a supported repository locator or select a different backend with --vcs."],
                )
            ]
        )
    try:
        return Ok(Dependency(vcs=selected, repository=locator, selector=request.selector))
    except pydantic.ValidationError as exc:
        return Err([InvalidArguments(reason=str(exc)).with_cause(exc)])


@app.command("add")
def add_dependency(  # noqa: CFQ002 — Typer exposes these independent command options.
    context: typer.Context,
    name: DependencyNameArgument,
    repository: RepositoryArgument,
    vcs: VcsOption = None,
    branch: BranchOption = None,
    tag: TagOption = None,
    rev: RevisionOption = None,
    redefine: RedefineOption = False,
) -> None:
    """Declare a named dependency and lock its selected immutable revision."""
    with command_context(context) as command:
        request = parse_repository(repository).unwrap()
        selectors = [
            VersionSelector(kind=kind, value=value)
            for kind, value in ((SelectorKind.branch, branch), (SelectorKind.tag, tag), (SelectorKind.revision, rev))
            if value is not None
        ]
        if len(selectors) > 1:
            Err([InvalidArguments(reason="Use at most one of --branch, --tag, or --rev.")]).unwrap()
        if selectors and request.selector is not None:
            Err(
                [
                    InvalidArguments(
                        reason="A source-version fragment cannot be combined with --branch, --tag, or --rev."
                    )
                ]
            ).unwrap()
        if selectors:
            request = request.replace(selector=selectors[0])
        cwd = Path.cwd()
        context.with_resource(open_workspace(cwd=PathInput(cwd), path=command.config_input)).unwrap()

        dependency = prepare_dependency(request, cwd=cwd, vcs=vcs).unwrap()
        dependency_add(
            name,
            dependency,
            source=get_adapter(dependency.vcs),
            hosting=get_hosting(dependency).unwrap(),
            redefine=redefine,
        ).unwrap()


@app.command("list")
def list_dependencies(context: typer.Context) -> None:
    """List declared dependencies, locked revisions, and local entry statistics."""
    with command_context(context) as command:
        context.with_resource(open_workspace(cwd=PathInput(Path.cwd()), path=command.config_input)).unwrap()

        dependency_list().unwrap()


@app.command("remove")
def remove_dependency(context: typer.Context, name: DependencyNameArgument) -> None:
    """Remove an unused dependency declaration and its lock, preserving installed files."""
    with command_context(context) as command:
        context.with_resource(open_workspace(cwd=PathInput(Path.cwd()), path=command.config_input)).unwrap()

        dependency_remove(name).unwrap()


@app.command("outdated")
def outdated_dependencies(context: typer.Context, names: DependencyNamesArgument = None) -> None:
    """Report dependencies whose configured source version differs from their locked revision."""
    with command_context(context) as command:
        cwd = Path.cwd()
        context.with_resource(open_workspace(cwd=PathInput(cwd), path=command.config_input)).unwrap()

        dependency_outdated(tuple(names or ())).unwrap()


@app.command("update")
def update_dependencies(
    context: typer.Context, names: DependencyNamesArgument = None, force: ForceOverwriteOption = False
) -> None:
    """Advance selected dependencies and refresh all their entries with local-change protection."""
    with command_context(context) as command:
        cwd = Path.cwd()
        context.with_resource(open_workspace(cwd=PathInput(cwd), path=command.config_input)).unwrap()

        dependency_update(tuple(names or ()), force=force).unwrap()
