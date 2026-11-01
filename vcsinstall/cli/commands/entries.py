from pathlib import Path

import typer
from llm_tool_cli.cli.context import command_context
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err
from llm_tool_cli.paths import PathInput, normalize_path

from vcsinstall.cache import active_cache_root, resolve_cache_root
from vcsinstall.cli.entities import (
    AllowOutdatedOption,
    DestinationArgument,
    EntryPathsArgument,
    ForceRemovalOption,
    KeepFilesOption,
    RedefineEntryOption,
    SourceArgument,
)
from vcsinstall.hosting import get_hosting
from vcsinstall.runtime import entry_add, entry_remove
from vcsinstall.sources import get_adapter
from vcsinstall.workspace import Entry, load_workspace


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
        cache_input = context.find_root().params.get("cache_dir")
        cache_root = resolve_cache_root(Path(cache_input) if cache_input is not None else None, cwd=cwd).unwrap()
        context.with_resource(active_cache_root.scope(cache_root))
        workspace = load_workspace(cwd=PathInput(cwd), path=command.config_input).unwrap()
        context.with_resource(workspace.scope())
        if source.dependency not in workspace.config.dependencies:
            Err([InvalidArguments(reason=f"Unknown dependency {source.dependency!r}.")]).unwrap()
        entry = Entry(
            dependency=source.dependency,
            source_path=source.source_path,
            destination=normalize_path(destination.strip(), workspace.project_root, cwd=cwd).unwrap(),
        )
        dependency = workspace.config.dependencies[source.dependency]
        entry_add(
            entry,
            source=get_adapter(dependency.vcs),
            hosting=get_hosting(dependency.vcs).unwrap(),
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
        workspace = load_workspace(cwd=PathInput(cwd), path=command.config_input).unwrap()
        context.with_resource(workspace.scope())
        destinations = tuple(
            normalize_path(path.strip(), workspace.project_root, cwd=cwd).unwrap() for path in entry_paths
        )
        entry_remove(destinations, keep_files=keep_files, force=force).unwrap()
