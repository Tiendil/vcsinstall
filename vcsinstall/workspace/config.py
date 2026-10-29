"""Project configuration declarations."""

from typing import Literal, NewType, Self

import pydantic
from llm_tool_cli.config.fields import ConfigVersion
from llm_tool_cli.core.entities import BaseEntity

from vcsinstall.domain import (
    Dependency,
    DependencyName,
    SourcePath,
)

DestinationInput = NewType("DestinationInput", str)


class Entry(BaseEntity):
    dependency: DependencyName
    source_path: SourcePath
    destination: DestinationInput = pydantic.Field(min_length=1)


class Config(BaseEntity):
    version: ConfigVersion[Literal[1]]
    dependencies: dict[DependencyName, Dependency] = pydantic.Field(default_factory=dict)
    entries: tuple[Entry, ...] = ()

    @pydantic.model_validator(mode="after")
    def _validate_bindings(self) -> Self:
        for index, entry in enumerate(self.entries):
            if entry.dependency not in self.dependencies:
                raise ValueError(f"entries[{index}].dependency refers to undeclared dependency {entry.dependency!r}")
        return self
