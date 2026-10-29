from llm_tool_cli.core.errors import EnvironmentErrors
from llm_tool_cli.core.result import Err, Ok, Result

from vcsinstall.domain import Dependency, SourceRevision


class RecordingAdapter:
    def __init__(self, problems: EnvironmentErrors | None = None) -> None:
        self.dependencies: list[Dependency] = []
        self.locks: list[tuple[Dependency, SourceRevision]] = []
        self.problems = problems

    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        self.dependencies.append(dependency)
        return Err(self.problems) if self.problems else Ok(None)

    def validate_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        self.locks.append((dependency, revision))
        return Err(self.problems) if self.problems else Ok(None)
