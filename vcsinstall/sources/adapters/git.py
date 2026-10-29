import re

from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error

from vcsinstall.domain import Dependency, SelectorValue, SourceRevision, VcsId
from vcsinstall.sources import errors


def _validate_reference(value: SelectorValue, *, branch: bool) -> Result[None]:
    """Check native ref spelling without requiring Git or repository access."""
    components = value.split("/")
    if (
        value.endswith(".")
        or ".." in value
        or "@{" in value
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
        or any(not part or part.startswith(".") or part.endswith(".lock") for part in components)
        or (branch and (value.startswith("-") or value == "HEAD"))
    ):
        return Err([errors.InvalidGitReference(field="branch" if branch else "tag", value=value)])
    return Ok(None)


def _validate_revision(value: str, *, field: str) -> Result[None]:
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
        return Err([errors.InvalidRevision(vcs=VcsId.git, field=field, value=value)])
    return Ok(None)


class GitSourceAdapter:
    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        if dependency.rev is not None:
            return _validate_revision(dependency.rev, field="rev")
        if dependency.branch is not None:
            return _validate_reference(dependency.branch, branch=True)
        if dependency.tag is not None:
            return _validate_reference(dependency.tag, branch=False)
        return Ok(None)

    @unwrap_to_error
    def validate_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        self.validate_dependency(dependency).unwrap()
        _validate_revision(revision, field="revision").unwrap()
        if dependency.version is None:
            return Err([errors.MissingGitSelector()])
        if dependency.rev is not None:
            # Both values have passed complete Git revision validation.
            if dependency.rev != revision:  # type: ignore[comparison-overlap]
                return Err([errors.RevisionMismatch(revision=revision, expected=dependency.rev)])
        return Ok(None)
