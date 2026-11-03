from pathlib import Path

import typer
from llm_tool_cli.cli.context import command_context
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import PathInput, normalize_path

from vcsinstall.cli.entities import (
    DependencyNameArgument,
    HostingProviderOption,
    MessageOption,
    PushBaseOption,
    PushBranchOption,
    PushEntryPathsArgument,
    PushModeOption,
    PushOutputOption,
    PushRepositoryOption,
)
from vcsinstall.cli.preparation import prepare_cache
from vcsinstall.domain import RepositoryLocator
from vcsinstall.hosting import PushOptions, get_hosting, validate_push_options
from vcsinstall.runtime import dependency_push
from vcsinstall.sources import SourceAdapter, get_adapter, parse_repository
from vcsinstall.workspace import open_workspace


def _filesystem_path(value: str, *, cwd: Path) -> Result[PathInput]:
    try:
        path = Path(value).expanduser()
    except (OSError, RuntimeError, ValueError) as exc:
        return Err([InvalidArguments(reason=str(exc)).with_cause(exc)])
    return Ok(PathInput(path if path.is_absolute() else cwd / path))


@unwrap_to_error
def _publication_repository(value: str, *, source: SourceAdapter, cwd: Path) -> Result[RepositoryLocator]:
    request = parse_repository(RepositoryLocator(value)).unwrap()
    if request.selector is not None:
        return Err([InvalidArguments(reason="--push-repository cannot contain a source-version fragment.")])
    repository = source.normalize_repository(request.repository, cwd=cwd).unwrap()
    if repository is None:
        return Err([InvalidArguments(reason="--push-repository must use the declared dependency's backend.")])
    return Ok(repository)


def _prepare_message(messages: list[str]) -> Result[str]:
    r"""Join explicit message paragraphs and require nonempty UTF-8 text.

    For example, ['Subject', 'Body'] becomes 'Subject\n\nBody'; blank text fails.
    """
    message = "\n\n".join(messages)
    if not message.strip():
        return Err([InvalidArguments(reason="A contribution message must be nonempty UTF-8 text.")])
    try:
        message.encode("utf-8")
    except UnicodeError as exc:
        return Err([InvalidArguments(reason="A contribution message must be valid UTF-8.").with_cause(exc)])
    return Ok(message)


def push_dependency(  # noqa: CFQ002, CCR001 — Prepare the independent public options in one command scope.
    context: typer.Context,
    name: DependencyNameArgument,
    mode: PushModeOption,
    entry_paths: PushEntryPathsArgument = None,
    branch: PushBranchOption = None,
    push_repository: PushRepositoryOption = None,
    base: PushBaseOption = None,
    hosting_provider: HostingProviderOption = None,
    output: PushOutputOption = None,
    message: MessageOption = None,
) -> None:
    """Deliver local changes from all or selected entries of one named dependency."""
    with command_context(context) as command:
        cwd = Path.cwd()
        if output is not None and (output == "-" or output.startswith("@") or not output):
            Err(
                [
                    InvalidArguments(
                        reason="--output requires a filesystem artifact path, without '-' or project notation."
                    )
                ]
            ).unwrap()
        options = PushOptions(
            mode=mode,
            branch=branch,
            base=base,
            push_repository=RepositoryLocator(push_repository) if push_repository is not None else None,
            patch_file=_filesystem_path(output, cwd=cwd).unwrap() if output is not None else None,
        )
        validate_push_options(options, has_message=message is not None).unwrap()
        prepare_cache(context, cwd=cwd).unwrap()
        workspace = context.with_resource(open_workspace(cwd=PathInput(cwd), path=command.config_input)).unwrap()

        definition = workspace.config.dependencies.get(name)
        if definition is None:
            Err([InvalidArguments(reason=f"Unknown dependency {name!r}.")]).unwrap()
        assert definition is not None
        source = get_adapter(definition.vcs)
        repository = source.normalize_repository(definition.repository, cwd=workspace.project_root).unwrap()
        definition = definition.replace(repository=repository or definition.repository)
        hosting = get_hosting(definition, hosting_provider).unwrap()
        hosting.validate_options(definition, options).unwrap()
        if push_repository is not None:
            options = options.replace(
                push_repository=_publication_repository(push_repository, source=source, cwd=cwd).unwrap()
            )
        destinations = tuple(
            normalize_path(path.strip(), workspace.project_root, cwd=cwd).unwrap() for path in entry_paths or ()
        )
        if message is not None:
            options = options.replace(message=_prepare_message(message).unwrap())
        dependency_push(name, destinations, options=options, source=source, hosting=hosting).unwrap()
