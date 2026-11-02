from enum import StrEnum

from llm_tool_cli.core.entities import BaseEntity

from vcsinstall.domain import DependencyName, SourceRevision
from vcsinstall.filesystem import ContentTree
from vcsinstall.sources import ResolvedSource
from vcsinstall.workspace import DependencyLock, Entry, InstallationBaseline


class LocalStatus(StrEnum):
    unmodified = "unmodified"
    modified = "modified"
    missing = "missing"
    unlocked = "unlocked"


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
    resolved: ResolvedSource
    lock: DependencyLock
    entries: list[EntryPlan]
    previous_revision: SourceRevision | None
    unchanged: bool
