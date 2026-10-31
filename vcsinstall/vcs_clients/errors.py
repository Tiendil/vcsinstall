from vcsinstall.core import errors as core_errors
from vcsinstall.domain import RepositoryLocator, VcsId


class InternalError(core_errors.InternalError):
    pass


class EnvironmentError(core_errors.EnvironmentError):
    pass


class CommandFailed(EnvironmentError):
    code: str = "vcs_command_failed"
    message: str = "Cannot run {error.vcs} for repository {error.repository!r}: {error.reason}"
    ways_to_fix: list[str] = [
        "Check that the native VCS client is installed and the repository location is correct.",
        "Use the native client diagnostic to correct configuration, access permissions, or authentication.",
    ]
    vcs: VcsId
    repository: RepositoryLocator
    reason: str
    returncode: int | None = None


class InvalidCommit(EnvironmentError):
    code: str = "git_invalid_commit"
    message: str = "Git object {error.value!r} in repository {error.repository!r} is not a commit."
    ways_to_fix: list[str] = [
        "Select a commit identifier, or use a branch or tag selector that resolves to a commit.",
    ]
    repository: RepositoryLocator
    value: str
