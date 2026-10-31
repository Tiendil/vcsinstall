from vcsinstall.hosting.adapters.filesystem import FilesystemHostingAdapter
from vcsinstall.hosting.adapters.git import GitHostingAdapter
from vcsinstall.hosting.interfaces import HostingAdapter
from vcsinstall.hosting.registry import DEFAULT_HOSTING_FACTORIES, HostingFactory, get_hosting

__all__ = [
    "DEFAULT_HOSTING_FACTORIES",
    "FilesystemHostingAdapter",
    "GitHostingAdapter",
    "HostingAdapter",
    "HostingFactory",
    "get_hosting",
]
