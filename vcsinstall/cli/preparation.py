"""Prepare cache settings for CLI commands."""

from pathlib import Path

import typer
from llm_tool_cli.core.result import Ok, Result, unwrap_to_error

from vcsinstall.cache import active_cache_root, resolve_cache_root


@unwrap_to_error
def prepare_cache(context: typer.Context, *, cwd: Path) -> Result[None]:
    """Resolve and bind the cache root without creating or locking cache storage."""
    cache_input = context.find_root().params.get("cache_dir")
    root = resolve_cache_root(Path(cache_input) if cache_input is not None else None, cwd=cwd).unwrap()
    context.with_resource(active_cache_root.scope(root))
    return Ok(None)
