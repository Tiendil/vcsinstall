from llm_tool_cli.core.entities import BaseEntity

from vcsinstall.filesystem import ContentTree
from vcsinstall.workspace import Entry, InstallationBaseline


class EntryPlan(BaseEntity):
    entry: Entry
    source: ContentTree
    observed: ContentTree
    previous: InstallationBaseline
    baseline: InstallationBaseline

    @property
    def changed(self) -> bool:
        return self.previous != self.baseline or self.observed.file_hashes != self.source.file_hashes
