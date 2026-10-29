from vcsinstall.core import errors as core_errors
from vcsinstall.domain import SelectorValue, SourceRevision, VcsId


class InternalError(core_errors.InternalError):
    """Root for internal source-adapter failures."""


class EnvironmentError(core_errors.EnvironmentError):
    """Root for expected source validation failures."""


class InvalidGitReference(EnvironmentError):
    code: str = "invalid_git_reference"
    message: str = "Invalid Git {error.field} name: {error.value!r}."
    field: str
    value: SelectorValue


class InvalidRevision(EnvironmentError):
    code: str = "invalid_source_revision"
    message: str = "Invalid complete immutable {error.vcs} {error.field}: {error.value!r}."
    vcs: VcsId
    field: str
    value: str


class UnsupportedSelector(EnvironmentError):
    code: str = "unsupported_source_selector"
    message: str = "{error.vcs} sources do not support {error.field} selectors."
    vcs: VcsId
    field: str


class MissingGitSelector(EnvironmentError):
    code: str = "missing_git_selector"
    message: str = "A Git lock requires exactly one of branch, tag, or rev."


class RevisionMismatch(EnvironmentError):
    code: str = "source_revision_mismatch"
    message: str = "Locked revision {error.revision!r} must equal the immutable rev selector {error.expected!r}."
    revision: SourceRevision
    expected: SelectorValue
