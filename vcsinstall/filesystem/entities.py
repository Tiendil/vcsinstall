from typing import Self

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
        """Require canonical, UTF-8-encodable file metadata paths without NUL bytes.

        Example: '@/guide.md' and the standalone-file position '@/' pass;
        'guide.md', noncanonical '@/docs/../guide', and paths containing NUL fail.
        """
        if not is_project_path_id(value) or "\x00" in value:
            raise ValueError("File metadata requires canonical repository-relative identifiers.")
        value.encode("utf-8")
        return value


class ContentTree(BaseEntity):
    """Root kind and regular files found within an inspection's requested scope."""

    kind: ContentKind
    files: tuple[FileInfo, ...] = ()

    @pydantic.model_validator(mode="after")
    def _validate_files(self) -> Self:  # noqa: CCR001 — Validate root kinds and paths together.
        """Reject duplicate or obstructed modeled file paths and impossible root kinds.

        A directory containing '@/a' and '@/a/b', or names collapsing to one path
        after stripping, fails. A file contains exactly one root-position record.
        """
        paths = {file.path for file in self.files}
        if len(paths) != len(self.files):
            raise ValueError("Inspected file paths must be unique after normalization.")
        if self.kind == ContentKind.file:
            if paths != {"@/"}:
                raise ValueError("An inspected file must have exactly one root-position file record.")
        elif self.kind == ContentKind.absent:
            if paths:
                raise ValueError("Absent content cannot contain files.")
        elif "@/" in paths or any(
            any(
                ProjectPathId(path[:index]) in paths
                for index, character in enumerate(path)
                if index > 1 and character == "/"
            )
            for path in paths
        ):
            raise ValueError("Directory file paths cannot contain a root file or obstruct each other.")
        return self

    @property
    def file_hashes(self) -> dict[ProjectPathId, ContentHash]:
        return {file.path: file.hash for file in self.files}
