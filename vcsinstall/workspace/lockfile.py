"""Persisted dependency locks, installation baselines, and lockfile loading."""

import stat
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Annotated, Literal, NewType, Self

import pydantic
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config import load_config
from llm_tool_cli.config.fields import ConfigVersion
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import ProjectPathId, resolve_inside_project

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
        """Require the stored directory hash to agree with its complete recorded manifest.

        Example: an empty manifest must use hashes.hash_files({}); a digest
        computed from a nonempty manifest is rejected for that empty baseline.
        """
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

    def entries_for_dependency(self, name: DependencyName) -> tuple[InstallationBaseline, ...]:
        """Return this dependency's installation baselines in their stored order."""
        return tuple(entry for entry in self.entries if entry.dependency == name)

    def baseline_for_destination(self, destination: ProjectPathId) -> InstallationBaseline | None:
        """Return the recorded baseline at a canonical destination, if one exists."""
        return next((entry for entry in self.entries if entry.destination == destination), None)

    def with_dependency(self, name: DependencyName, dependency: DependencyLock) -> Self:
        """Return a copy adding or replacing one shared lock without changing entry baselines."""
        return self.replace(dependencies=self.dependencies | {name: dependency})

    def with_baselines(self, baselines: Iterable[InstallationBaseline]) -> Self:
        """Return a copy replacing baselines in place and appending new destinations in input order.

        Repeated destinations use the last supplied baseline. Runtime decides
        which baselines may change; unrelated records and shared locks remain.
        """
        recorded = {baseline.destination: baseline for baseline in self.entries}
        recorded.update((baseline.destination, baseline) for baseline in baselines)
        return self.replace(entries=tuple(recorded.values()))

    def without_entries(self, destinations: Collection[ProjectPathId]) -> Self:
        """Return a copy removing exact baseline destinations while preserving all shared locks.

        Unknown destinations are ignored; selecting '@/docs' does not remove
        a baseline at '@/docs/guide'. The original order of retained records remains.
        """
        selected = set(destinations)
        return self.replace(entries=tuple(entry for entry in self.entries if entry.destination not in selected))

    @pydantic.model_validator(mode="after")
    def _validate_bindings(self) -> Self:
        """Require every baseline to name a dependency recorded in this lockfile.

        Example: a baseline with dependency='ci' fails if dependencies has no
        'ci' lock, even if configuration declares that name.
        """
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
