from pathlib import Path
from typing import ClassVar

import pydantic
from llm_tool_cli.core import errors as shared_errors
from llm_tool_cli.core.entities import ExitCode
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.core import errors as core_errors
from vcsinstall.domain import DependencyName


class InternalError(core_errors.InternalError):
    """Root for internal workspace failures."""


class EnvironmentError(core_errors.EnvironmentError):
    """Root for expected workspace failures."""


class DependencyLockRequired(EnvironmentError):
    code: str = "dependency_lock_required"
    message: str = "{error.path}: dependency {error.dependency!r} has no matching revision lock."
    ways_to_fix: list[str] = [
        "Run install or dependency update {error.dependency} to establish a matching revision lock."
    ]
    path: Path
    dependency: DependencyName


class MissingBaseline(EnvironmentError):
    code: str = "missing_baseline"
    message: str = (
        "{error.path}: dependency {error.dependency!r} has no installation baseline "
        "for declared destination {error.destination}."
    )
    ways_to_fix: list[str] = [
        "Restore the entry's recorded installation baseline from version control if lock state was lost.",
        "Remove the entry declaration if it was added accidentally.",
    ]
    path: Path
    dependency: DependencyName
    destination: ProjectPathId


class OrphanedBaseline(EnvironmentError):
    code: str = "orphaned_baseline"
    message: str = (
        "{error.path}: the installation baseline for dependency {error.dependency!r} at "
        "{error.destination} has no corresponding configuration entry."
    )
    ways_to_fix: list[str] = [
        "Restore the entry declaration for this dependency and destination if its removal was accidental.",
        "If the mapping change was intentional, reconcile the obsolete lockfile record with the configuration.",
    ]
    path: Path
    dependency: DependencyName
    destination: ProjectPathId


class BaselineMismatch(EnvironmentError):
    code: str = "baseline_mismatch"
    message: str = (
        "{error.path}: the installation baseline at {error.destination} does not match "
        "the declared source mapping for dependency {error.dependency!r}."
    )
    ways_to_fix: list[str] = [
        "Restore the matching declaration or recorded baseline before verifying local content.",
        "Use remove --keep-files to retain the content or remove --force to authorize deletion without verification.",
    ]
    path: Path
    dependency: DependencyName
    destination: ProjectPathId


class DependencyValidation(EnvironmentError):
    cli_exit_code: ClassVar[ExitCode] = ExitCode.configuration_error
    code: str = "dependency_validation_failed"
    message: str = "{error.path}: dependencies.{error.dependency}: {error.reason}"
    path: Path
    dependency: DependencyName
    problem: pydantic.SerializeAsAny[shared_errors.EnvironmentError]

    @property
    def reason(self) -> str:
        return self.problem.format_message()

    def as_record(self) -> dict[str, object]:
        record = super().as_record()
        record.pop("problem")
        record.update(
            {
                "source_" + key: value
                for key, value in self.problem.as_record().items()
                if key not in {"type", "message"}
            }
        )
        return record
