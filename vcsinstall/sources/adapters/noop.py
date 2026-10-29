from llm_tool_cli.core.result import Err, Ok, Result

from vcsinstall.domain import Dependency, SourceRevision, VcsId
from vcsinstall.sources import errors


class NoopSourceAdapter:
    """Validate opaque source identifiers without consulting fixture state."""

    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        return Ok(None)

    def validate_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        if not revision:
            return Err([errors.InvalidRevision(vcs=VcsId.noop, field="revision", value=revision)])
        if dependency.rev is not None:
            # The requested pin and locked revision use the same opaque representation.
            if dependency.rev != revision:  # type: ignore[comparison-overlap]
                return Err([errors.RevisionMismatch(revision=revision, expected=dependency.rev)])
        return Ok(None)
