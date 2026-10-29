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
from llm_tool_cli.paths import PathInput, ProjectConfigPath, ProjectRootPath

from vcsinstall.domain import Dependency, DependencyName, SourceRevision
from vcsinstall.sources import DEFAULT_ADAPTERS, AdapterRegistry, get_adapter
from vcsinstall.workspace import errors
from vcsinstall.workspace.config import Config
from vcsinstall.workspace.lockfile import Lockfile, LockfilePath, load_lockfile


class Workspace(BaseEntity):
    config_path: ProjectConfigPath
    lockfile_path: LockfilePath
    config: Config
    lockfile: Lockfile | None

    @property
    def project_root(self) -> ProjectRootPath:
        return ProjectRootPath(self.config_path.parent)

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
    def with_context(problems: EnvironmentErrors) -> EnvironmentErrors:
        return [errors.DependencyValidation(path=path, dependency=name, problem=problem) for problem in problems]

    adapter = get_adapter(dependency.vcs, adapters=adapters)
    result = (
        adapter.validate_dependency(dependency) if revision is None else adapter.validate_lock(dependency, revision)
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
    return Ok(
        Workspace(config_path=selected.config_path, lockfile_path=lockfile_path, config=config, lockfile=lockfile)
    )


@unwrap_to_error
def load_workspace(
    *, cwd: PathInput, path: ProjectConfigPath | None = None, adapters: AdapterRegistry = DEFAULT_ADAPTERS
) -> Result[Workspace]:
    """Load a workspace without activating it in the caller's context."""
    with load_project(Config, cwd=cwd, path=path) as loaded:
        return _prepare_workspace(loaded.unwrap(), adapters=adapters)
