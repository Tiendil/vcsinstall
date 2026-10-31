"""Project configuration declarations."""

from typing import Literal, Self

import pydantic
from llm_tool_cli.config.fields import ConfigVersion
from llm_tool_cli.core.entities import BaseEntity

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

    @pydantic.model_validator(mode="after")
    def _validate_bindings(self) -> Self:
        for index, entry in enumerate(self.entries):
            if entry.dependency not in self.dependencies:
                raise ValueError(f"entries[{index}].dependency refers to undeclared dependency {entry.dependency!r}")
        return self
