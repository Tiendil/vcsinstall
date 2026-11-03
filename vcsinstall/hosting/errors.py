from vcsinstall.core import errors as core_errors
from vcsinstall.domain import RepositoryLocator, SourcePath, SourceRevision


class InternalError(core_errors.InternalError):
    pass


class EnvironmentError(core_errors.EnvironmentError):
    pass


class PreparationFailed(EnvironmentError):
    code: str = "publication_preparation_failed"
    message: str = "Cannot prepare changes for {error.repository!r}: {error.reason}"
    ways_to_fix: list[str] = ["Resolve the reported content, destination, or native-client problem and retry."]
    repository: RepositoryLocator
    reason: str


class BranchExists(EnvironmentError):
    code: str = "publication_branch_exists"
    message: str = "Contribution branch {error.branch!r} already exists in {error.repository!r}."
    ways_to_fix: list[str] = ["Choose a new contribution branch; existing branches are never replaced."]
    repository: RepositoryLocator
    branch: str


class DestinationConflict(EnvironmentError):
    code: str = "publication_destination_conflict"
    message: str = (
        "Destination {error.source_path} in {error.repository!r} at {error.revision} "
        "differs from both the selected local content and its installation baseline."
    )
    ways_to_fix: list[str] = [
        "Reconcile the complete selected entry with the destination before retrying direct delivery."
    ]
    repository: RepositoryLocator
    source_path: SourcePath
    revision: SourceRevision


class PublicationFailed(EnvironmentError):
    code: str = "publication_failed"
    message: str = (
        "Publication to {error.repository!r} did not complete: {error.reason} "
        "Completed effects: {error.completed_effects}. Uncertain outcome: {error.uncertain}."
    )
    ways_to_fix: list[str] = [
        "Inspect the reported recipient, branch, and review state before retrying.",
        "For a PR retry, supply the reported contribution branch and the same message, selection, and base.",
    ]
    repository: RepositoryLocator
    branch: str | None = None
    revision: SourceRevision | None = None
    base_branch: str | None = None
    completed_effects: list[str] = []
    uncertain: bool = False
    reason: str


class ServiceFailed(EnvironmentError):
    code: str = "hosting_service_failed"
    message: str = "Cannot use hosting service {error.host!r}: {error.reason}"
    ways_to_fix: list[str] = [
        "Check the installed gh client, its configured account and host, and the reported service diagnostic."
    ]
    host: str
    reason: str
