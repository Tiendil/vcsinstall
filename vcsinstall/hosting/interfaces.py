from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from llm_tool_cli.core.result import Result
from llm_tool_cli.paths import ResolvedProjectPath

from vcsinstall.domain import Dependency, RepositoryLocator, SourceRevision
from vcsinstall.hosting.entities import DeliveryResult, PublicationRequest, PushOptions
from vcsinstall.sources import SourceAdapter


class HostingAdapter(Protocol):
    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        """Return False for rejected input and preserve remote-access failures as errors."""
        ...

    def acquire(
        self, dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> AbstractContextManager[Result[Path]]:
        """Expose a normalized repository path and protect access until the context exits.

        Refresh version metadata when requested, independently of acquiring the
        required immutable revision. Cached objects may satisfy a revision-only
        request without contacting the source.
        """
        ...

    def validate_artifact(self, dependency: Dependency, path: ResolvedProjectPath | None) -> Result[None]:
        """Check local artifact exclusions without acquisition or service requests.

        A Git patch may not overwrite the source's .git marker or administrative storage.
        """
        ...

    def validate_options(self, dependency: Dependency, options: PushOptions) -> Result[None]:
        """Check delivery choices without reading payloads or accessing repositories.

        For example, a tag-pinned direct request requires an explicit destination branch.
        """
        ...

    def publish(self, request: PublicationRequest, *, source: SourceAdapter) -> Result[DeliveryResult]:
        """Prepare and complete the selected outgoing workflow without changing project state."""
        ...
