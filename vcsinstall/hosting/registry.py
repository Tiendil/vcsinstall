from collections.abc import Callable, Mapping
from types import MappingProxyType

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result

from vcsinstall.cache import active_cache_root
from vcsinstall.domain import Dependency, VcsId
from vcsinstall.hosting.adapters.filesystem import FilesystemHostingAdapter
from vcsinstall.hosting.adapters.git import GitHostingAdapter
from vcsinstall.hosting.adapters.github import GitHubHostingAdapter, infer_github
from vcsinstall.hosting.entities import HostingProvider
from vcsinstall.hosting.interfaces import HostingAdapter

type HostingFactory = Callable[[], Result[HostingAdapter]]

DEFAULT_HOSTING_FACTORIES: Mapping[HostingProvider, HostingFactory] = MappingProxyType(
    {
        HostingProvider.git: lambda: Ok(GitHostingAdapter(root=active_cache_root.get())),
        HostingProvider.github: lambda: Ok(GitHubHostingAdapter(root=active_cache_root.get())),
        HostingProvider.filesystem: lambda: Ok(FilesystemHostingAdapter()),
    }
)


def get_hosting(dependency: Dependency, provider: HostingProvider | None = None) -> Result[HostingAdapter]:
    """Select hosting from normalized source identity and an override, independently of the operation.

    Optional service detection never makes native Git depend on service-client access.
    Selection may inspect local client metadata but does not acquire repositories or create caches.
    """
    if dependency.vcs == VcsId.noop:
        return Err([InvalidArguments(reason="A controlled noop hosting adapter must be supplied explicitly.")])

    if provider is None and dependency.vcs == VcsId.git:
        provider = (
            HostingProvider.github if infer_github(dependency.repository).unwrap_or(False) else HostingProvider.git
        )
    elif provider is None:
        provider = HostingProvider.filesystem

    if (dependency.vcs == VcsId.filesystem) != (provider == HostingProvider.filesystem):
        return Err(
            [
                InvalidArguments(
                    reason=f"Hosting provider {provider} is incompatible with source backend {dependency.vcs}.",
                    ways_to_fix=["Select a hosting provider compatible with the dependency's source backend."],
                )
            ]
        )
    return DEFAULT_HOSTING_FACTORIES[provider]()
