from llm_tool_cli.core.context import ContextValue

from vcsinstall.cache.entities import CacheRootPath

# TODO: Consider moving this setting into runtime_config owned by llm_tool_cli.
active_cache_root = ContextValue[CacheRootPath]("vcsinstall_cache_root")
