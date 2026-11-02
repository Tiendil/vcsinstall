from llm_tool_cli.core.result import Result, unwrap_to_error
from llm_tool_cli.output import output

from vcsinstall.cache import active_cache_root, clean_cache
from vcsinstall.runtime.cells import CacheCell, Outcome


@unwrap_to_error
def cache_clean() -> Result[None]:
    """Clean the selected shared cache and emit one result, including for an absent cache."""
    root = active_cache_root.get()
    changed = clean_cache(root).unwrap()
    return output.get().emit([CacheCell(cache_dir=root, outcome=Outcome.cleaned if changed else Outcome.unchanged)])
