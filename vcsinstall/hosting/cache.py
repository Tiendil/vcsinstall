"""Repository cache policy for hosting operations."""

from llm_tool_cli.paths import CacheRootPath, PathSegment
from llm_tool_cli.tool_cache import ToolCache

LOCK_TIMEOUT_SECONDS = 10


def repository_cache(root: CacheRootPath) -> ToolCache:
    """Select repository storage and timeout policy within the shared cache."""
    return ToolCache(root=root, directory=PathSegment("repos"), timeout=LOCK_TIMEOUT_SECONDS)
