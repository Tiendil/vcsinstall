"""Source adapters and explicit registry selection."""

from vcsinstall.sources import errors
from vcsinstall.sources.entities import ResolvedSource, SourceRequest
from vcsinstall.sources.interfaces import SourceAdapter
from vcsinstall.sources.locators import parse_repository
from vcsinstall.sources.registry import (
    DEFAULT_ADAPTERS,
    DETECTION_ORDER,
    AdapterRegistry,
    get_adapter,
)

__all__ = [
    "DEFAULT_ADAPTERS",
    "DETECTION_ORDER",
    "AdapterRegistry",
    "SourceAdapter",
    "ResolvedSource",
    "SourceRequest",
    "errors",
    "get_adapter",
    "parse_repository",
]
