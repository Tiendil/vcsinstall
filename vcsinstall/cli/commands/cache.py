from pathlib import Path

import typer
from llm_tool_cli.cli.context import command_context

from vcsinstall.cli.preparation import prepare_cache
from vcsinstall.runtime import cache_clean

app = typer.Typer(
    help="Manage the shared repository cache.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.command("clean")
def clean(context: typer.Context) -> None:
    """Clear owned cached repositories without changing projects or original sources."""
    with command_context(context):
        prepare_cache(context, cwd=Path.cwd()).unwrap()
        cache_clean().unwrap()
