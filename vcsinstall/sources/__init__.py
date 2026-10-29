"""Source adapters and explicit registry selection."""

from vcsinstall.sources import errors
from vcsinstall.sources.interfaces import SourceAdapter
from vcsinstall.sources.registry import DEFAULT_ADAPTERS, AdapterRegistry, get_adapter

__all__ = ["DEFAULT_ADAPTERS", "AdapterRegistry", "SourceAdapter", "errors", "get_adapter"]
