from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from llm_tool_cli.core.result import Result

from vcsinstall.domain import Dependency, RepositoryLocator, SourceRevision


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
