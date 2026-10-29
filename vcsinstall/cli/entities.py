from typing import Annotated

import pydantic
import typer
from llm_tool_cli.core.entities import BaseEntity

from vcsinstall.domain import DependencyName, RepositoryLocator, SourcePath


def _parse_dependency_name(value: str) -> DependencyName:
    try:
        return pydantic.TypeAdapter(DependencyName).validate_python(value)
    except pydantic.ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


DependencyNameArgument = Annotated[
    DependencyName,
    typer.Argument(
        metavar="NAME",
        parser=_parse_dependency_name,
        help="Project-local dependency name.",
    ),
]

RepositoryArgument = Annotated[
    RepositoryLocator,
    typer.Argument(
        metavar="REPOSITORY",
        parser=RepositoryLocator,
        help="Source repository locator.",
    ),
]


class DependencySource(BaseEntity):
    """A parsed dependency source reference, before project lookup."""

    dependency: DependencyName
    source_path: SourcePath

    @pydantic.model_validator(mode="before")
    @classmethod
    def _parse_reference(cls, value: object) -> object:
        if isinstance(value, str):
            dependency, anchor, path = value.partition("@/")
            if not anchor:
                raise ValueError("Expected a dependency source reference in NAME@/PATH form")
            return {"dependency": dependency, "source_path": anchor + path}
        return value
