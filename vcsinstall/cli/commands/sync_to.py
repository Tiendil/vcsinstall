from pathlib import Path

import typer
from llm_tool_cli.cli.context import command_context
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err
from llm_tool_cli.paths import PathInput

from vcsinstall.cli.entities import DependencyNameArgument, ForceSyncOption, SourceDirectoryArgument
from vcsinstall.runtime import dependency_sync_to
from vcsinstall.workspace import open_workspace


def sync_dependency_to(
    context: typer.Context,
    name: DependencyNameArgument,
    source_dir: SourceDirectoryArgument,
    force: ForceSyncOption = False,
) -> None:
    """Mirror changed entries into their original source paths under SOURCE_DIR."""
    with command_context(context) as command:
        context.with_resource(open_workspace(cwd=PathInput(Path.cwd()), path=command.config_input)).unwrap()
        try:
            directory = PathInput(Path(source_dir).expanduser())
        except (OSError, RuntimeError, ValueError) as exc:
            Err([InvalidArguments(reason=str(exc)).with_cause(exc)]).unwrap()
            return
        dependency_sync_to(name, directory, force=force).unwrap()
