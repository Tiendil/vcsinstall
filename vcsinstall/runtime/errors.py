from llm_tool_cli.paths import ProjectPathId

from vcsinstall.core import errors as core_errors
from vcsinstall.domain import DependencyName, SourcePath, SourceRevision, VersionSelector


class InternalError(core_errors.InternalError):
    pass


class EnvironmentError(core_errors.EnvironmentError):
    pass


class LocalConflict(EnvironmentError):
    code: str = "local_content_conflict"
    message: str = "Local content at {error.destination} differs from both its baseline and the selected source."
    ways_to_fix: list[str] = [
        "Preserve your local changes elsewhere or make the complete entry content match "
        "its baseline or selected source, then retry."
    ]
    destination: str


class InstallationWriteFailed(EnvironmentError):
    code: str = "installation_write_failed"
    message: str = (
        "Installing files failed; installed content may be inconsistent with the project records. "
        "The project lock has not advanced."
    )
    ways_to_fix: list[str] = ["Resolve the reported failure, inspect the installed entries, and retry."]


class RemovalConflict(EnvironmentError):
    code: str = "removal_conflict"
    message: str = "Existing local content at {error.destination} differs from its recorded installation baseline."
    ways_to_fix: list[str] = [
        "Preserve your changes elsewhere or use remove --keep-files to retain the content.",
        "Use remove --force to authorize deletion of local changes within the selected entry roots.",
    ]
    destination: ProjectPathId


class RemovalWriteFailed(EnvironmentError):
    code: str = "removal_write_failed"
    message: str = (
        "Removing {error.destination} failed; installed content may be inconsistent with project records. "
        "Project records have not been changed."
    )
    ways_to_fix: list[str] = ["Resolve the reported failure, inspect the selected entries, and retry removal."]
    destination: ProjectPathId


class OutdatedDependency(EnvironmentError):
    code: str = "outdated_dependency"
    message: str = (
        "Dependency {error.dependency!r} ({error.selector_description}) is locked at {error.locked_revision}, "
        "but currently selects {error.current_revision}."
    )
    ways_to_fix: list[str] = [
        "Run dependency update {error.dependency} or use add --allow-outdated to install the locked revision."
    ]
    dependency: DependencyName
    selector: VersionSelector | None
    locked_revision: SourceRevision
    current_revision: SourceRevision

    @property
    def selector_description(self) -> str:
        return f"{self.selector.kind} {self.selector.value!r}" if self.selector else "default selector"


class DependencyCheckFailed(EnvironmentError):
    code: str = "dependency_check_failed"
    message: str = "Cannot check the current revision of dependency {error.dependency!r}."
    ways_to_fix: list[str] = ["Resolve the accompanying source-access or revision-resolution diagnostics and retry."]
    dependency: DependencyName


class DependencyUpdateFailed(EnvironmentError):
    code: str = "dependency_update_failed"
    message: str = "Cannot update dependency {error.dependency!r}."
    ways_to_fix: list[str] = ["Resolve the accompanying diagnostics and retry dependency update."]
    dependency: DependencyName


class SourcePathAbsent(EnvironmentError):
    code: str = "source_path_absent"
    message: str = "Dependency {error.dependency!r} has no source path {error.source_path} at {error.revision}."
    ways_to_fix: list[str] = ["Select a path that exists at the dependency's locked revision."]
    dependency: DependencyName
    source_path: SourcePath
    revision: SourceRevision


class SourceIntegrityMismatch(EnvironmentError):
    code: str = "source_integrity_mismatch"
    message: str = "Source content for {error.destination} differs from its recorded installation baseline."
    ways_to_fix: list[str] = ["Restore trustworthy source data or lock state before retrying."]
    destination: ProjectPathId
