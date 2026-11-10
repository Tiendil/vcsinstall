from pathlib import Path

from llm_tool_cli.core.entities import BaseEntity

from vcsinstall.domain import Dependency, RepositoryLocator, SourceRevision, VersionSelector


class SourceRequest(BaseEntity):
    """Repository locator and version intent before backend selection."""

    repository: RepositoryLocator
    selector: VersionSelector | None = None


class ResolvedSource(BaseEntity):
    dependency: Dependency
    path: Path
    revision: SourceRevision
