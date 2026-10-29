from pathlib import Path

import typer
from llm_tool_cli.cli.context import command_context
from llm_tool_cli.paths import PathInput

from vcsinstall.cli.entities import DependencyNameArgument, RepositoryArgument
from vcsinstall.runtime import dependency_add
from vcsinstall.workspace import load_workspace

app = typer.Typer(
    help="Manage named dependencies.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.command("add")
def add_dependency(context: typer.Context, name: DependencyNameArgument, repository: RepositoryArgument) -> None:
    """Validate project files only; dependency creation is not implemented yet."""
    with command_context(context) as command:
        workspace = load_workspace(cwd=PathInput(Path.cwd()), path=command.config_input).unwrap()
        context.with_resource(workspace.scope())
        dependency_add(name, repository).unwrap()
