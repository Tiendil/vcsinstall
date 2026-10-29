from pathlib import Path
from typing import ClassVar

import pydantic
from llm_tool_cli.core import errors as shared_errors
from llm_tool_cli.core.entities import ExitCode

from vcsinstall.core import errors as core_errors
from vcsinstall.domain import DependencyName


class InternalError(core_errors.InternalError):
    """Root for internal workspace failures."""


class EnvironmentError(core_errors.EnvironmentError):
    """Root for expected workspace failures."""


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
