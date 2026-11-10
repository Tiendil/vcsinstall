"""Persisted manifest and content record models."""

from abc import ABC, abstractmethod
from typing import Annotated, Literal

import pydantic
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import ProjectPathId, is_project_path_id, project_path_parts

from vcsinstall.domain import ContentHash, ContentKind
from vcsinstall.filesystem import hashes


class ManifestFile(BaseEntity):
    """A regular file identified below its entry root with a shared canonical path."""

    path: ProjectPathId
    hash: ContentHash

    @pydantic.field_validator("path")
    @classmethod
    def _validate_file_path(cls, path: ProjectPathId) -> ProjectPathId:
        """Require a canonical manifest child strictly below its entry root.

        Example: '@/guide.md' passes, while '@/' and 'guide.md' fail. The root
        file position '@/' is not a directory-manifest child.
        """
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
        """Reject duplicate or ancestor file positions and return files in path order.

        Example: '@/a' together with '@/a/b' fails, as do two '@/a' records.
        Independent '@/b' and '@/a' records pass and are returned as a, b.
        """
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
    """Verify a directory aggregate against its recorded file hashes, without I/O.

    Example: an empty directory with hashes.hash_files({}) passes; an empty
    directory with a different digest fails. File and absent content are
    returned unchanged because they have no aggregate manifest hash to check.
    """
    if isinstance(content, DirectoryContent) and content.hash != hashes.hash_files(content.file_hashes()):
        raise ValueError("Stored directory hash does not match its file manifest")
    return content


ContentSchema = Annotated[Content, pydantic.Field(discriminator="kind"), pydantic.AfterValidator(_verify_content_hash)]


def _canonical_path(value: ProjectPathId) -> ProjectPathId:
    """Require an already canonical identifier rather than normalizing path text.

    Example: '@/docs/guide' passes; 'docs/guide' and '@/docs/../guide' fail,
    even though the latter could be normalized into another identifier.
    """
    if not is_project_path_id(value):
        raise ValueError(f"Expected a canonical @/ identifier: {value!r}")
    return value


type CanonicalPath = Annotated[ProjectPathId, pydantic.AfterValidator(_canonical_path)]
