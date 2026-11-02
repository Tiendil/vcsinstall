from pathlib import Path
from typing import Annotated

import pydantic
import typer
from llm_tool_cli.core.entities import BaseEntity

from vcsinstall.domain import DependencyName, RepositoryLocator, SelectorValue, SourcePath, VcsId


def _parse_dependency_name(value: str) -> DependencyName:
    """Validate a command-line dependency name and report malformed values to Typer.

    Example: 'ci-tools' passes; 'CiTools' and 'ci/tools' fail as BadParameter.
    """
    try:
        return pydantic.TypeAdapter(DependencyName).validate_python(value)
    except pydantic.ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _parse_selector_value(value: str) -> SelectorValue:
    """Strip surrounding selector whitespace and require a nonempty literal value.

    Example: ' main ' becomes 'main', while whitespace-only input fails.
    A literal 'release+draft' is preserved without URL decoding.
    """
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

DependencyNamesArgument = Annotated[
    list[DependencyName] | None,
    typer.Argument(
        metavar="NAME...",
        parser=_parse_dependency_name,
        help="Dependency names; omit to check every declared dependency.",
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
        """Split source-reference syntax before dependency and source-path validation.

        Example: 'ci@/docs' becomes dependency='ci', source_path='@/docs';
        'ci/docs' fails because it has no '@/'. Model fields validate the
        extracted dependency name and normalize its anchored path afterward.
        """
        if isinstance(value, str):
            dependency, anchor, path = value.partition("@/")
            if not anchor:
                raise ValueError("Expected a dependency source reference in NAME@/PATH form")
            return {"dependency": dependency, "source_path": anchor + path}
        return value


def _parse_dependency_source(value: str) -> DependencySource:
    """Validate a complete command-line dependency source reference.

    Example: 'ci@/docs/../LICENSE' becomes ci with source path '@/LICENSE';
    'ci@/../LICENSE' and 'ci/docs' fail as BadParameter.
    """
    try:
        return DependencySource.model_validate(value)
    except pydantic.ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


SourceArgument = Annotated[
    DependencySource,
    typer.Argument(
        metavar="SOURCE", parser=_parse_dependency_source, help="Declared dependency path in NAME@/PATH form."
    ),
]

DestinationArgument = Annotated[
    str,
    typer.Argument(metavar="DESTINATION", help="Final project path for the file or directory."),
]

RedefineEntryOption = Annotated[
    bool,
    typer.Option("--redefine", help="Redefine the entry at this destination, preserving local-change protection."),
]

AllowOutdatedOption = Annotated[
    bool,
    typer.Option("--allow-outdated", help="Permit installation from an outdated matching dependency lock."),
]

EntryPathsArgument = Annotated[
    list[str],
    typer.Argument(
        metavar="ENTRY_PATH...", help="Declared entry roots, using project identifiers or contained paths."
    ),
]

ListEntryPathsArgument = Annotated[
    list[str] | None,
    typer.Argument(
        metavar="ENTRY_PATH...",
        help="Declared entry roots, using project identifiers or contained paths; omit to list every entry.",
    ),
]

KeepFilesOption = Annotated[
    bool,
    typer.Option("--keep-files", help="Remove entry metadata and retain the installed content as unmanaged files."),
]

ForceRemovalOption = Annotated[
    bool,
    typer.Option("--force", help="Allow deletion of modified content or content without a matching baseline."),
]

LockedOption = Annotated[
    bool,
    typer.Option("--locked", help="Require a complete matching lockfile and leave its bytes unchanged."),
]
