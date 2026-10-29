from collections.abc import Mapping
from types import MappingProxyType

from vcsinstall.domain import VcsId
from vcsinstall.sources.adapters.filesystem import FilesystemSourceAdapter
from vcsinstall.sources.adapters.git import GitSourceAdapter
from vcsinstall.sources.adapters.noop import NoopSourceAdapter
from vcsinstall.sources.interfaces import SourceAdapter

type AdapterRegistry = Mapping[VcsId, SourceAdapter]

DEFAULT_ADAPTERS: AdapterRegistry = MappingProxyType(
    {
        VcsId.git: GitSourceAdapter(),
        VcsId.filesystem: FilesystemSourceAdapter(),
        VcsId.noop: NoopSourceAdapter(),
    }
)


def get_adapter(vcs: VcsId, *, adapters: AdapterRegistry = DEFAULT_ADAPTERS) -> SourceAdapter:
    """Select a backend from a registry covering every recognized VCS identifier."""
    return adapters[vcs]
