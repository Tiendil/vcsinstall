from collections.abc import Callable, Mapping
from types import MappingProxyType

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result

from vcsinstall.cache import active_cache_root
from vcsinstall.domain import VcsId
from vcsinstall.hosting.adapters.filesystem import FilesystemHostingAdapter
from vcsinstall.hosting.adapters.git import GitHostingAdapter
from vcsinstall.hosting.interfaces import HostingAdapter

type HostingFactory = Callable[[], Result[HostingAdapter]]

DEFAULT_HOSTING_FACTORIES: Mapping[VcsId, HostingFactory] = MappingProxyType(
    {
        VcsId.git: lambda: Ok(GitHostingAdapter(root=active_cache_root.get())),
        VcsId.filesystem: lambda: Ok(FilesystemHostingAdapter()),
        VcsId.noop: lambda: Err(
            [InvalidArguments(reason="A controlled noop hosting adapter must be supplied explicitly.")]
        ),
    }
)


def get_hosting(vcs: VcsId) -> Result[HostingAdapter]:
    """Select native acquisition without accessing the repository or cache."""
    return DEFAULT_HOSTING_FACTORIES[vcs]()
