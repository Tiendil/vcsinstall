"""Project configuration declarations."""

from collections.abc import Collection
from typing import Literal, Self

import pydantic
from llm_tool_cli.config.fields import ConfigVersion
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import (
    Dependency,
    DependencyName,
    SourcePath,
)
from vcsinstall.workspace.entities import CanonicalPath


class Entry(BaseEntity):
    dependency: DependencyName
    source_path: SourcePath
    destination: CanonicalPath


class Config(BaseEntity):
    version: ConfigVersion[Literal[1]]
    dependencies: dict[DependencyName, Dependency] = pydantic.Field(default_factory=dict)
    entries: tuple[Entry, ...] = ()

    def entries_for_dependency(self, name: DependencyName) -> tuple[Entry, ...]:
        """Return this dependency's entries in their stored order."""
        return tuple(entry for entry in self.entries if entry.dependency == name)

    def entry_for_destination(self, destination: ProjectPathId) -> Entry | None:
        """Return the declaration at a canonical destination, if one exists."""
        return next((entry for entry in self.entries if entry.destination == destination), None)

    def with_dependency(self, name: DependencyName, dependency: Dependency) -> Self:
        """Return a copy adding or replacing one dependency without changing entry mappings."""
        return self.replace(dependencies=self.dependencies | {name: dependency})

    def without_dependency(self, name: DependencyName) -> Self:
        """Return a copy removing one declaration, preserving all entry mappings.

        The caller must first ensure the dependency is unused. An unknown name
        leaves the data unchanged; unrelated declarations retain their order.
        """
        return self.replace(dependencies={key: value for key, value in self.dependencies.items() if key != name})

    def with_entry(self, entry: Entry) -> Self:
        """Return a copy replacing an exact destination in place, or appending a new entry.

        The caller establishes operation permissions and destination ownership;
        this method only updates declaration data using the entry's identity.
        """
        declarations = {item.destination: item for item in self.entries}
        declarations[entry.destination] = entry
        return self.replace(entries=tuple(declarations.values()))

    def without_entries(self, destinations: Collection[ProjectPathId]) -> Self:
        """Return a copy removing exact destinations and preserving dependencies and entry order.

        Unknown destinations are ignored; selecting '@/docs' does not remove
        a declaration at '@/docs/guide'. Selection validity belongs to runtime.
        """
        selected = set(destinations)
        return self.replace(entries=tuple(entry for entry in self.entries if entry.destination not in selected))

    @pydantic.model_validator(mode="after")
    def _validate_bindings(self) -> Self:
        """Require every entry to name a dependency declared in this configuration.

        Example: an entry with dependency='ci' fails when dependencies lacks
        'ci'. Declaring 'ci' without any entries is valid.
        """
        for index, entry in enumerate(self.entries):
            if entry.dependency not in self.dependencies:
                raise ValueError(f"entries[{index}].dependency refers to undeclared dependency {entry.dependency!r}")
        return self
