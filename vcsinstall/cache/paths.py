from pathlib import Path

from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import resolve_project_root
from platformdirs import user_cache_path

from vcsinstall.cache import errors
from vcsinstall.cache.entities import CacheRootPath


def _default_cache_root() -> Result[Path]:
    try:
        location = user_cache_path("vcsinstall", appauthor=False, opinion=False, ensure_exists=False)
        if location.is_absolute():
            return Ok(location)
    except (OSError, RuntimeError):
        pass
    try:
        return Ok(Path.home() / ".cache" / "vcsinstall")
    except (OSError, RuntimeError) as exc:
        return Err([errors.AccessFailed(path=Path("."), reason=str(exc)).with_cause(exc)])


@unwrap_to_error
def resolve_cache_root(value: Path | None, *, cwd: Path) -> Result[CacheRootPath]:
    """Select and normalize a cache root without creating its directory."""
    try:
        selected = _default_cache_root().unwrap() if value is None else value
        expanded = selected.expanduser()
    except (OSError, RuntimeError) as exc:
        return Err([errors.AccessFailed(path=cwd, reason=str(exc)).with_cause(exc)])
    path = resolve_project_root(expanded if expanded.is_absolute() else cwd / expanded).unwrap()
    return Ok(CacheRootPath(path))
