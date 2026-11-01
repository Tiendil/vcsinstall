"""Workspace loading and explicit activation scopes."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from llm_tool_cli.config import load_project
from llm_tool_cli.config.project import ProjectConfig, project_config
from llm_tool_cli.core.context import ContextValue
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.core.errors import EnvironmentErrors
from llm_tool_cli.core.result import Ok, Result, unwrap_to_error
from llm_tool_cli.paths import PathInput, ProjectConfigPath, ProjectPathId, ProjectRootPath

from vcsinstall.domain import Dependency, DependencyName, SourceRevision
from vcsinstall.sources import DEFAULT_ADAPTERS, AdapterRegistry, SourceAdapter, get_adapter
from vcsinstall.workspace import errors, validation
from vcsinstall.workspace.config import Config, Entry
from vcsinstall.workspace.lockfile import DependencyLock, InstallationBaseline, Lockfile, LockfilePath, load_lockfile


class Workspace(BaseEntity):
    config_path: ProjectConfigPath
    lockfile_path: LockfilePath
    config: Config
    lockfile: Lockfile | None

    @property
    def project_root(self) -> ProjectRootPath:
        return ProjectRootPath(self.config_path.parent)

    def validate_no_orphaned_baselines(self, dependency: DependencyName) -> Result[None]:
        """Require recorded installations to remain declared for this dependency.

        Example: a ci baseline at '@/old' fails when no ci declaration owns
        that destination, even if another dependency now declares it.
        """
        return validation.validate_no_orphaned_baselines(self, dependency)

    def validate_no_missing_baselines(self, dependency: DependencyName) -> Result[None]:
        """Require an existing baseline for every declared destination of this dependency.

        Example: ci declaring '@/docs' fails when no ci baseline records that
        destination; missing baselines of unselected dependencies are ignored.
        """
        return validation.validate_no_missing_baselines(self, dependency)

    def require_matching_dependency_lock(
        self, dependency: DependencyName, *, source: SourceAdapter
    ) -> Result[DependencyLock]:
        """Compare prepared repository locators and selectors, returning the existing lock without I/O.

        Example: a Git branch 'main' declaration cannot reuse a tag 'main'
        lock. A matching record is returned without resolving a newer revision.
        """
        return validation.require_matching_dependency_lock(self, dependency, source=source)

    def require_entry_baseline(self, entry: Entry) -> Result[InstallationBaseline]:
        """Require a recorded baseline at the supplied declaration's destination.

        Example: an entry at '@/docs' returns its prior baseline there; no
        recorded baseline produces MissingBaseline rather than inventing one.
        """
        return validation.require_entry_baseline(self, entry)

    def require_matching_entry_baseline(self, entry: Entry) -> Result[InstallationBaseline]:
        """Return a baseline for the same destination, dependency, and source path.

        Example: '@/docs' declared from ci '@/guide' rejects an old baseline
        from ci '@/other'. Matching the dependency lock is a separate check.
        """
        return validation.require_matching_entry_baseline(self, entry)

    def validate_destination_ownership(self, destination: ProjectPathId) -> Result[None]:
        """Check destination ownership without authorizing replacement or accessing files.

        Example: existing '@/docs' rejects proposed '@/docs/guide', while the
        exact destination '@/docs' is left for runtime's replacement check.
        """
        return validation.validate_destination_ownership(self, destination)

    @contextmanager
    def scope(self) -> Iterator[None]:
        """Activate this loaded workspace and restore previous bindings on exit."""
        with project_config.scope(ProjectConfig(config_path=self.config_path)), active_workspace.scope(self):
            yield


active_workspace = ContextValue[Workspace]("vcsinstall_workspace")


def _validate_source(
    dependency: Dependency,
    *,
    name: DependencyName,
    path: Path,
    adapters: AdapterRegistry,
    revision: SourceRevision | None = None,
) -> Result[None]:
    """Validate backend fields and attach the dependency name and project-file path to errors.

    Example: Git branch 'bad..branch' for dependencies.ci becomes a
    DependencyValidation error identifying ci and the selected configuration
    file. Supplying revision also validates the recorded lock fields.
    """

    def with_context(problems: EnvironmentErrors) -> EnvironmentErrors:
        return [errors.DependencyValidation(path=path, dependency=name, problem=problem) for problem in problems]

    adapter = get_adapter(dependency.vcs, adapters=adapters)
    result = (
        adapter.validate_dependency(dependency)
        if revision is None
        else adapter.validate_revision_lock(dependency, revision)
    )
    return result.map_err(with_context)


@unwrap_to_error
def _prepare_workspace(config: Config, *, adapters: AdapterRegistry) -> Result[Workspace]:
    selected = project_config.get()
    lockfile_path = LockfilePath(selected.project_dir / "vcsinstall.lock")
    for name, dependency in config.dependencies.items():
        _validate_source(dependency, name=name, path=selected.config_path, adapters=adapters).unwrap()
    lockfile = load_lockfile(lockfile_path).unwrap()
    if lockfile is not None:
        for name, locked_dependency in lockfile.dependencies.items():
            _validate_source(
                locked_dependency,
                name=name,
                path=lockfile_path,
                adapters=adapters,
                revision=locked_dependency.revision,
            ).unwrap()
    workspace = Workspace(
        config_path=selected.config_path,
        lockfile_path=lockfile_path,
        config=config,
        lockfile=lockfile,
    )
    validation.validate_workspace_destinations(workspace).unwrap()
    return Ok(workspace)


@unwrap_to_error
def load_workspace(
    *, cwd: PathInput, path: ProjectConfigPath | None = None, adapters: AdapterRegistry = DEFAULT_ADAPTERS
) -> Result[Workspace]:
    """Load a workspace without activating it in the caller's context."""
    with load_project(Config, cwd=cwd, path=path) as loaded:
        return _prepare_workspace(loaded.unwrap(), adapters=adapters)
