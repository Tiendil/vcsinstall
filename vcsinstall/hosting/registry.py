from collections.abc import Callable, Mapping
from types import MappingProxyType

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config.runtime import runtime_config
from llm_tool_cli.core.result import Err, Ok, Result

from vcsinstall.domain import Dependency, VcsId
from vcsinstall.hosting.adapters.filesystem import FilesystemHostingAdapter
from vcsinstall.hosting.adapters.git import GitHostingAdapter
from vcsinstall.hosting.interfaces import HostingAdapter

type HostingFactory = Callable[[], Result[HostingAdapter]]

DEFAULT_HOSTING_FACTORIES: Mapping[VcsId, HostingFactory] = MappingProxyType(
    {
        VcsId.git: lambda: Ok(GitHostingAdapter(root=runtime_config.get().cache_dir)),
        VcsId.filesystem: lambda: Ok(FilesystemHostingAdapter()),
    }
)


def get_hosting(dependency: Dependency) -> Result[HostingAdapter]:
    """Select acquisition by backend, including native Git for every Git host."""
    factory = DEFAULT_HOSTING_FACTORIES.get(dependency.vcs)
    if factory is None:
        return Err([InvalidArguments(reason="A controlled noop hosting adapter must be supplied explicitly.")])
    return factory()
