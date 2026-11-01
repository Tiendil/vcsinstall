from enum import StrEnum
from typing import Annotated, NewType

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
    Annotated[str, pydantic.StringConstraints(min_length=1, strip_whitespace=True)],
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


class ContentKind(StrEnum):
    file = "file"
    directory = "directory"
    absent = "absent"


class SelectorKind(StrEnum):
    branch = "branch"
    tag = "tag"
    revision = "revision"
    unresolved = "unresolved"


class VersionSelector(BaseEntity):
    kind: SelectorKind
    value: SelectorValue


class Dependency(BaseEntity):
    """A source declaration, independent of adapters and persisted project state."""

    vcs: VcsId
    repository: RepositoryLocator = pydantic.Field(min_length=1)
    selector: VersionSelector | None = None
