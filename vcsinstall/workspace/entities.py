"""Persisted manifest and content record models."""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Annotated, Literal

import pydantic
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import ProjectPathId, is_project_path_id, project_path_parts

from vcsinstall.domain import ContentHash
from vcsinstall.filesystem import hashes


class ContentKind(StrEnum):
    file = "file"
    directory = "directory"
    absent = "absent"


class ManifestFile(BaseEntity):
    """A regular file identified below its entry root with a shared canonical path."""

    path: Annotated[ProjectPathId, pydantic.StringConstraints(strict=True, strip_whitespace=False)]
    hash: ContentHash

    @pydantic.field_validator("path")
    @classmethod
    def _validate_file_path(cls, path: ProjectPathId) -> ProjectPathId:
        if not is_project_path_id(path) or path == "@/":
            raise ValueError("A manifest file must have a canonical path strictly below its entry root")

        return path


class ContentBase(BaseEntity, ABC):
    """Common data interface for content at one entry root."""

    kind: ContentKind

    @abstractmethod
    def file_hashes(self) -> dict[ProjectPathId, ContentHash]:
        """Return file hashes at canonical positions scoped to the entry root."""
        ...


class FileContent(ContentBase):
    """The content identity of one standalone regular file."""

    kind: Literal[ContentKind.file] = ContentKind.file
    hash: ContentHash

    def file_hashes(self) -> dict[ProjectPathId, ContentHash]:
        return {ProjectPathId("@/"): self.hash}


class DirectoryContent(ContentBase):
    """A complete directory selection with a canonical file manifest."""

    kind: Literal[ContentKind.directory] = ContentKind.directory
    hash: ContentHash
    files: tuple[ManifestFile, ...]

    @pydantic.field_validator("files")
    @classmethod
    def _validate_files(cls, files: tuple[ManifestFile, ...]) -> tuple[ManifestFile, ...]:
        paths = {project_path_parts(file.path) for file in files}
        if len(paths) != len(files):
            raise ValueError("Manifest paths must be unique")
        for path in paths:
            if any(path[:end] in paths for end in range(1, len(path))):
                raise ValueError(f"A manifest file must not be an ancestor of another file: {path!r}")
        return tuple(sorted(files, key=lambda file: file.path))

    def file_hashes(self) -> dict[ProjectPathId, ContentHash]:
        return {file.path: file.hash for file in self.files}


class AbsentContent(ContentBase):
    """An absent content root, without a file hash or manifest."""

    kind: Literal[ContentKind.absent] = ContentKind.absent

    def file_hashes(self) -> dict[ProjectPathId, ContentHash]:
        return {}


Content = FileContent | DirectoryContent | AbsentContent


def _verify_content_hash(content: Content) -> Content:
    if isinstance(content, DirectoryContent) and content.hash != hashes.hash_files(content.file_hashes()):
        raise ValueError("Stored directory hash does not match its file manifest")
    return content


ContentSchema = Annotated[Content, pydantic.Field(discriminator="kind"), pydantic.AfterValidator(_verify_content_hash)]
