from pathlib import Path

import typer
from llm_tool_cli.cli.context import command_context

from vcsinstall.cache import active_cache_root, resolve_cache_root
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
        cache_input = context.find_root().params.get("cache_dir")
        root = resolve_cache_root(Path(cache_input) if cache_input is not None else None, cwd=Path.cwd()).unwrap()
        context.with_resource(active_cache_root.scope(root))
        cache_clean().unwrap()
