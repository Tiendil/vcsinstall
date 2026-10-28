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


BackendName = NewType("BackendName", str)
DependencyName = NewType(
    "DependencyName",
    Annotated[str, pydantic.StringConstraints(pattern=r"\A[a-z][a-z0-9_-]*\z", strict=True, strip_whitespace=False)],
)
RepositoryLocator = NewType("RepositoryLocator", str)
SelectorValue = NewType("SelectorValue", str)
SourcePath = NewType(
    "SourcePath",
    Annotated[
        ProjectPathId,
        pydantic.StringConstraints(strict=True, strip_whitespace=False),
        pydantic.AfterValidator(_normalize_source_path),
    ],
)
SourceRevision = NewType("SourceRevision", str)


class SelectorKind(StrEnum):
    branch = "branch"
    tag = "tag"
    revision = "revision"


class RepositoryIdentity(BaseEntity):
    """A backend and its native locator, with backend normalization supplied by the caller."""

    backend: Annotated[BackendName, pydantic.StringConstraints(min_length=1, strip_whitespace=False, strict=True)]
    locator: Annotated[
        RepositoryLocator, pydantic.StringConstraints(min_length=1, strip_whitespace=False, strict=True)
    ]


class VersionSelector(BaseEntity):
    """An explicit selection; omitted/default selection is represented by None."""

    kind: SelectorKind
    value: Annotated[SelectorValue, pydantic.StringConstraints(min_length=1, strip_whitespace=False, strict=True)]
