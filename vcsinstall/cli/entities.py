from pathlib import Path
from typing import Annotated

import pydantic
import typer
from llm_tool_cli.core.entities import BaseEntity

from vcsinstall.domain import DependencyName, RepositoryLocator, SelectorValue, SourcePath, VcsId


def _parse_dependency_name(value: str) -> DependencyName:
    try:
        return pydantic.TypeAdapter(DependencyName).validate_python(value)
    except pydantic.ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _parse_selector_value(value: str) -> SelectorValue:
    try:
        return pydantic.TypeAdapter(SelectorValue).validate_python(value)
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


CacheDirOption = Annotated[
    Path | None,
    typer.Option("--cache-dir", help="Shared repository cache root."),
]

VcsOption = Annotated[
    VcsId | None,
    typer.Option("--vcs", help="Select the source backend."),
]

BranchOption = Annotated[
    SelectorValue | None,
    typer.Option("--branch", parser=_parse_selector_value, help="Track an exact branch."),
]

TagOption = Annotated[
    SelectorValue | None,
    typer.Option("--tag", parser=_parse_selector_value, help="Select an exact tag."),
]

RevisionOption = Annotated[
    SelectorValue | None,
    typer.Option("--rev", parser=_parse_selector_value, help="Pin an immutable revision."),
]

RedefineOption = Annotated[
    bool,
    typer.Option("--redefine", help="Redefine a dependency and refresh all its entries."),
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
