import typer
from llm_tool_cli.cli.commands.cache import register_cache_clean_command
from llm_tool_cli.config.runtime import runtime_config
from llm_tool_cli.core.result import Ok

from vcsinstall.hosting import repository_cache

app = typer.Typer(
    help="Manage the shared repository cache.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


register_cache_clean_command(
    app,
    cache_factory=lambda context: Ok(repository_cache(runtime_config.get().cache_dir)),
    help="Clear owned cached repositories without changing projects or original sources.",
)
