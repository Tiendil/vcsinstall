import pydantic
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import ProjectPathId, is_project_path_id

from vcsinstall.domain import ContentHash, ContentKind


class FileInfo(BaseEntity):
    """A selected file's identity and executable metadata."""

    path: ProjectPathId
    hash: ContentHash
    executable: bool = False

    @pydantic.field_validator("path")
    @classmethod
    def _validate_path(cls, value: ProjectPathId) -> ProjectPathId:
        if not is_project_path_id(value) or "\x00" in value:
            raise ValueError("File metadata requires canonical repository-relative identifiers.")
        value.encode("utf-8")
        return value


class ContentTree(BaseEntity):
    """Root kind and regular files found within an inspection's requested scope."""

    kind: ContentKind
    files: tuple[FileInfo, ...] = ()

    @property
    def file_hashes(self) -> dict[ProjectPathId, ContentHash]:
        return {file.path: file.hash for file in self.files}
