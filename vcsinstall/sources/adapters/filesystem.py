import re

from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error

from vcsinstall.domain import Dependency, SourceRevision, VcsId
from vcsinstall.sources import errors


def _validate_revision(value: str, *, field: str) -> Result[None]:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        return Err([errors.InvalidRevision(vcs=VcsId.filesystem, field=field, value=value)])
    return Ok(None)


class FilesystemSourceAdapter:
    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        if dependency.branch is not None:
            return Err([errors.UnsupportedSelector(vcs=VcsId.filesystem, field="branch")])
        if dependency.tag is not None:
            return Err([errors.UnsupportedSelector(vcs=VcsId.filesystem, field="tag")])
        if dependency.rev is not None:
            return _validate_revision(dependency.rev, field="rev")
        return Ok(None)

    @unwrap_to_error
    def validate_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        self.validate_dependency(dependency).unwrap()
        _validate_revision(revision, field="revision").unwrap()
        if dependency.rev is not None:
            # Both values have passed complete filesystem fingerprint validation.
            if dependency.rev != revision:  # type: ignore[comparison-overlap]
                return Err([errors.RevisionMismatch(revision=revision, expected=dependency.rev)])
        return Ok(None)
