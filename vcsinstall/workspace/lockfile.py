"""Persisted dependency locks, installation baselines, and lockfile loading."""

import stat
from pathlib import Path
from typing import Annotated, Literal, NewType, Self

import pydantic
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config import load_config
from llm_tool_cli.config.fields import ConfigVersion
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import resolve_inside_project

from vcsinstall.domain import Dependency, DependencyName, SourceRevision
from vcsinstall.workspace.entities import (
    AbsentContent,
    CanonicalPath,
    DirectoryContent,
    FileContent,
    _verify_content_hash,
)

LockfilePath = NewType("LockfilePath", Path)


class DependencyLock(Dependency):
    revision: SourceRevision


class Baseline(BaseEntity):
    dependency: DependencyName
    source_path: CanonicalPath
    destination: CanonicalPath


class FileBaseline(Baseline, FileContent):
    pass


class DirectoryBaseline(Baseline, DirectoryContent):
    @pydantic.model_validator(mode="after")
    def _validate_hash(self) -> Self:
        _verify_content_hash(self)
        return self


class AbsentBaseline(Baseline, AbsentContent):
    pass


type InstallationBaseline = Annotated[
    FileBaseline | DirectoryBaseline | AbsentBaseline, pydantic.Field(discriminator="kind")
]


class Lockfile(BaseEntity):
    version: ConfigVersion[Literal[1]]
    dependencies: dict[DependencyName, DependencyLock] = pydantic.Field(default_factory=dict)
    entries: tuple[InstallationBaseline, ...] = ()

    @pydantic.model_validator(mode="after")
    def _validate_bindings(self) -> Self:
        for index, entry in enumerate(self.entries):
            if entry.dependency not in self.dependencies:
                raise ValueError(f"entries[{index}].dependency refers to missing dependency lock {entry.dependency!r}")
        return self


@unwrap_to_error
def load_lockfile(path: LockfilePath) -> Result[Lockfile | None]:
    """Load exactly the selected sibling, distinguishing absent and invalid state."""
    root = project_config.get().project_dir
    resolve_inside_project(path, root).unwrap()
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return Ok(None)
    except OSError as exc:
        return Err([config_errors.Unreadable(path=path, reason=str(exc)).with_cause(exc)])
    if not stat.S_ISREG(mode):
        return Err([config_errors.Unreadable(path=path, reason="Lockfile must be a regular file")])
    return load_config(path, Lockfile)
