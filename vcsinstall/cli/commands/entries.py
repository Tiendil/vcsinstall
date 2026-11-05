from pathlib import Path

import typer
from llm_tool_cli.cli.context import command_context
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err
from llm_tool_cli.paths import PathInput, normalize_path

from vcsinstall.cli.entities import (
    AllowOutdatedOption,
    DestinationArgument,
    EntryPathsArgument,
    ForceRemovalOption,
    KeepFilesOption,
    ListEntryPathsArgument,
    LockedOption,
    RedefineEntryOption,
    SourceArgument,
)
from vcsinstall.domain import VcsId
from vcsinstall.runtime import entry_add, entry_list, entry_remove, install
from vcsinstall.workspace import Entry, open_workspace


def add_entry(
    context: typer.Context,
    source: SourceArgument,
    destination: DestinationArgument,
    redefine: RedefineEntryOption = False,
    allow_outdated: AllowOutdatedOption = False,
) -> None:
    """Declare and install a file or directory at its dependency's locked revision."""
    with command_context(context) as command:
        cwd = Path.cwd()
        workspace = context.with_resource(open_workspace(cwd=PathInput(cwd), path=command.config_input)).unwrap()

        if source.dependency not in workspace.config.dependencies:
            Err([InvalidArguments(reason=f"Unknown dependency {source.dependency!r}.")]).unwrap()
        entry = Entry(
            dependency=source.dependency,
            source_path=source.source_path,
            destination=normalize_path(destination.strip(), workspace.project_root, cwd=cwd).unwrap(),
        )
        entry_add(
            entry,
            redefine=redefine,
            allow_outdated=allow_outdated,
        ).unwrap()


def remove_entries(
    context: typer.Context,
    entry_paths: EntryPathsArgument,
    keep_files: KeepFilesOption = False,
    force: ForceRemovalOption = False,
) -> None:
    """Remove declared entries and their content, or keep the files unmanaged."""
    with command_context(context) as command:
        cwd = Path.cwd()
        workspace = context.with_resource(open_workspace(cwd=PathInput(cwd), path=command.config_input)).unwrap()

        destinations = tuple(
            normalize_path(path.strip(), workspace.project_root, cwd=cwd).unwrap() for path in entry_paths
        )
        entry_remove(destinations, keep_files=keep_files, force=force).unwrap()


def list_entries(context: typer.Context, entry_paths: ListEntryPathsArgument = None) -> None:
    """List declared entries, their locked revisions, and local changes."""
    with command_context(context) as command:
        cwd = Path.cwd()
        workspace = context.with_resource(open_workspace(cwd=PathInput(cwd), path=command.config_input)).unwrap()

        destinations = tuple(
            normalize_path(path.strip(), workspace.project_root, cwd=cwd).unwrap() for path in entry_paths or ()
        )
        entry_list(destinations).unwrap()


def install_entries(context: typer.Context, locked: LockedOption = False) -> None:
    """Install all declared entries, reusing matching dependency revisions."""
    with command_context(context) as command:
        cwd = Path.cwd()
        workspace = context.with_resource(open_workspace(cwd=PathInput(cwd), path=command.config_input)).unwrap()

        unsupported = sorted(
            name for name, dependency in workspace.config.dependencies.items() if dependency.vcs == VcsId.noop
        )
        if unsupported:
            Err(
                [
                    InvalidArguments(
                        reason=f"Dependency {name!r} uses noop, which is available only to test environments."
                    )
                    for name in unsupported
                ]
            ).unwrap()
        install(locked=locked).unwrap()
