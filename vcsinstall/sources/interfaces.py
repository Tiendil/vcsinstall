from typing import Protocol

from llm_tool_cli.core.result import Result

from vcsinstall.domain import Dependency, SourceRevision


class SourceAdapter(Protocol):
    """Backend-owned, effect-free validation of declarations and locked revisions."""

    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        """Validate backend-specific declaration fields without external access."""
        ...

    def validate_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        """Validate a declaration and its recorded immutable revision together."""
        ...
