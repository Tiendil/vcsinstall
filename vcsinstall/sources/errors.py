from vcsinstall.core import errors as core_errors
from vcsinstall.domain import RepositoryLocator, SelectorValue, SourceRevision, VcsId


class InternalError(core_errors.InternalError):
    """Root for internal source-adapter failures."""


class EnvironmentError(core_errors.EnvironmentError):
    """Root for expected source validation failures."""


class InvalidGitReference(EnvironmentError):
    code: str = "invalid_git_reference"
    message: str = "Invalid Git {error.field} name: {error.value!r}."
    ways_to_fix: list[str] = [
        "Use a valid literal Git {error.field} name, without spaces, revision expressions, or invalid ref characters.",
    ]
    field: str
    value: SelectorValue


class InvalidRevision(EnvironmentError):
    code: str = "invalid_source_revision"
    message: str = "Invalid complete immutable {error.vcs} {error.field}: {error.value!r}."
    ways_to_fix: list[str] = ["Use a complete immutable revision in the format required by {error.vcs}."]
    vcs: VcsId
    field: str
    value: str


class InvalidGitRevision(InvalidRevision):
    ways_to_fix: list[str] = [
        "Use the full lowercase Git commit identifier (40 or 64 hexadecimal digits) for {error.field}.",
    ]
    vcs: VcsId = VcsId.git


class InvalidFilesystemRevision(InvalidRevision):
    ways_to_fix: list[str] = [
        "Use sha256: followed by 64 lowercase hexadecimal digits for {error.field}.",
    ]
    vcs: VcsId = VcsId.filesystem


class InvalidNoopRevision(InvalidRevision):
    ways_to_fix: list[str] = ["Use a nonempty opaque fixture revision identifier for {error.field}."]
    vcs: VcsId = VcsId.noop


class UnsupportedSelector(EnvironmentError):
    code: str = "unsupported_source_selector"
    message: str = "{error.vcs} sources do not support {error.field} selectors."
    ways_to_fix: list[str] = ["Choose a selector supported by {error.vcs}."]
    vcs: VcsId
    field: str


class UnsupportedGitSelector(UnsupportedSelector):
    ways_to_fix: list[str] = [
        "Resolve the reference to a branch, tag, or revision selector "
        "before recording it in configuration or lock state.",
    ]
    vcs: VcsId = VcsId.git


class UnsupportedFilesystemSelector(UnsupportedSelector):
    ways_to_fix: list[str] = [
        "Use a complete sha256: revision fingerprint, "
        "or omit the selector to track the directory's current content.",
    ]
    vcs: VcsId = VcsId.filesystem


class UnsupportedNoopSelector(UnsupportedSelector):
    ways_to_fix: list[str] = ["Use a branch, tag, or revision selector with the literal fixture identifier."]
    vcs: VcsId = VcsId.noop


class MissingGitSelector(EnvironmentError):
    code: str = "missing_git_selector"
    message: str = "A Git lock requires exactly one of branch, tag, or rev."
    ways_to_fix: list[str] = [
        "Restore the dependency's recorded selector from a valid lockfile, "
        "or set it to the intended branch, tag, or revision.",
    ]


class RevisionMismatch(EnvironmentError):
    code: str = "source_revision_mismatch"
    message: str = "Locked revision {error.revision!r} must equal the immutable rev selector {error.expected!r}."
    ways_to_fix: list[str] = [
        "Restore source content and lock state matching the intended pinned revision {error.expected!r}.",
    ]
    revision: SourceRevision
    expected: SelectorValue


class RepositoryAccess(EnvironmentError):
    code: str = "source_access_failed"
    message: str = "Cannot access {error.vcs} source {error.repository!r}: {error.reason}"
    ways_to_fix: list[str] = [
        "Check the repository locator, read permissions, and availability of the requested revision and paths.",
        "Resolve the reported source problem and select supported files and directories.",
    ]
    vcs: VcsId
    repository: RepositoryLocator
    reason: str


class MissingNoopFixture(RepositoryAccess):
    ways_to_fix: list[str] = ["Supply a controlled source adapter with a fixture for {error.repository!r}."]
    vcs: VcsId = VcsId.noop
    reason: str = "No controlled source fixture was supplied."
