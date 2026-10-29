from enum import StrEnum
from typing import Annotated, NewType, Self

import pydantic
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import ProjectPathId, normalize_project_path_id


def _normalize_source_path(value: str) -> ProjectPathId:
    result = normalize_project_path_id(value)
    if result.is_err():
        raise ValueError(result.unwrap_err()[0].format_message())
    return result.unwrap()


ContentHash = NewType(
    "ContentHash",
    Annotated[str, pydantic.StringConstraints(pattern=r"\Asha256:[0-9a-f]{64}\z")],
)
DependencyName = NewType(
    "DependencyName",
    Annotated[str, pydantic.StringConstraints(pattern=r"\A[a-z][a-z0-9_-]*\z")],
)
RepositoryLocator = NewType("RepositoryLocator", str)
SelectorValue = NewType(
    "SelectorValue",
    Annotated[str, pydantic.StringConstraints(min_length=1)],
)
SourcePath = NewType(
    "SourcePath",
    Annotated[ProjectPathId, pydantic.AfterValidator(_normalize_source_path)],
)
SourceRevision = NewType("SourceRevision", str)


class VcsId(StrEnum):
    git = "git"
    filesystem = "filesystem"
    noop = "noop"


class Dependency(BaseEntity):
    """A source declaration, independent of adapters and persisted project state."""

    vcs: VcsId
    repository: RepositoryLocator = pydantic.Field(min_length=1)
    branch: SelectorValue | None = None
    tag: SelectorValue | None = None
    rev: SelectorValue | None = None

    @property
    def version(self) -> SelectorValue | None:
        """Return the selected tag, branch, or revision value, when declared."""
        return self.tag or self.branch or self.rev

    @pydantic.model_validator(mode="after")
    def _validate_selection(self) -> Self:
        if sum(value is not None for value in (self.branch, self.tag, self.rev)) > 1:
            raise ValueError("At most one of branch, tag, and rev may be present")
        return self
