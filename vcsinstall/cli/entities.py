import pydantic
from llm_tool_cli.core.entities import BaseEntity

from vcsinstall.domain import DependencyName, SourcePath


class DependencySource(BaseEntity):
    """A parsed dependency source reference, before project lookup."""

    dependency: DependencyName
    source_path: SourcePath

    @pydantic.model_validator(mode="before")
    @classmethod
    def _parse_reference(cls, value: object) -> object:
        if isinstance(value, str):
            dependency, anchor, path = value.partition("@/")
            if not anchor:
                raise ValueError("Expected a dependency source reference in NAME@/PATH form")
            return {"dependency": dependency, "source_path": anchor + path}
        return value
