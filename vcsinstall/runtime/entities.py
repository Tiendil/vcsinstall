from enum import StrEnum

import pydantic
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import ProjectPathId, ProjectRootPath

from vcsinstall.domain import ContentHash, Dependency, DependencyName, SourcePath, SourceRevision
from vcsinstall.filesystem import ContentTree
from vcsinstall.sources import ResolvedSource
from vcsinstall.workspace import DependencyLock, Entry, InstallationBaseline


class LocalStatus(StrEnum):
    unmodified = "unmodified"
    modified = "modified"
    missing = "missing"
    unlocked = "unlocked"


class LocalContentPolicy(StrEnum):
    protect = "protect"
    overwrite = "overwrite"


class EntryPlan(BaseEntity):
    entry: Entry
    source: ContentTree
    observed: ContentTree
    previous: InstallationBaseline | None
    baseline: InstallationBaseline

    @property
    def changed(self) -> bool:
        return self.previous != self.baseline or self.observed.file_hashes != self.source.file_hashes


class DependencyPlan(BaseEntity):
    """Prepared lock and entry changes, independently of command-specific configuration edits."""

    name: DependencyName
    definition: Dependency
    resolved: ResolvedSource | None
    lock: DependencyLock
    entries: list[EntryPlan]
    previous_revision: SourceRevision | None
    lock_unchanged: bool
    unchanged: bool


class DependencyRequest(BaseEntity):
    """Prepared dependency intent with optional observations for reusing an installation."""

    name: DependencyName
    definition: Dependency
    lock: DependencyLock | None = None
    observed: dict[ProjectPathId, ContentTree] = pydantic.Field(default_factory=dict)
    retained: list[EntryPlan] = pydantic.Field(default_factory=list)


class SyncEntry(BaseEntity):
    """One complete local mapping and its prepared recipient content."""

    source_path: SourcePath
    destination: ProjectPathId
    content: ContentTree
    baseline: dict[ProjectPathId, ContentHash]
    status: LocalStatus
    recipient: ContentTree

    @property
    def changed(self) -> bool:
        return self.content.file_hashes != self.recipient.file_hashes


class SyncPlan(BaseEntity):
    """Prepared local synchronization without repository clients or project mutations."""

    name: DependencyName
    definition: Dependency
    revision: SourceRevision
    source_dir: ProjectRootPath
    entries: tuple[SyncEntry, ...]
