import hashlib
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pydantic
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import (
    ProjectPathId,
    ProjectRootPath,
    resolve_inside_project,
    resolve_root_anchored_path,
)

from vcsinstall.domain import ContentHash, ContentKind
from vcsinstall.filesystem import errors
from vcsinstall.filesystem.entities import ContentTree, FileInfo


class FSClient:
    """Filesystem operations contained within one normalized root."""

    __slots__ = ("_root", "_readonly")

    def __init__(self, *, root: ProjectRootPath, readonly: bool = True) -> None:
        self._root = root
        self._readonly = readonly

    @property
    def root(self) -> ProjectRootPath:
        return self._root

    @property
    def readonly(self) -> bool:
        return self._readonly

    def _require_writable(self, path: Path) -> Result[None]:
        if self.readonly:
            return Err([errors.ReadOnly(root=self.root, path=path)])
        return Ok(None)

    @unwrap_to_error
    def write_file(self, path: ProjectPathId, data: bytes) -> Result[None]:
        """Write bytes directly to a file whose parent already exists."""
        self._require_writable(self.root / path.removeprefix("@/")).unwrap()
        target = resolve_root_anchored_path(path, self.root).unwrap()
        try:
            target.write_bytes(data)
        except OSError as exc:
            return Err([errors.AccessFailed(path=target, reason=str(exc)).with_cause(exc)])
        return Ok(None)

    @unwrap_to_error
    def remove(self, path: ProjectPathId) -> Result[None]:
        """Remove one file or complete directory tree, leaving its parents intact."""
        self._require_writable(self.root / path.removeprefix("@/")).unwrap()
        target = resolve_root_anchored_path(path, self.root).unwrap()
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        except OSError as exc:
            return Err([errors.AccessFailed(path=target, reason=str(exc)).with_cause(exc)])
        return Ok(None)

    @unwrap_to_error
    def inspect_kind(self, path: ProjectPathId = ProjectPathId("@/")) -> Result[ContentKind]:
        target = resolve_root_anchored_path(path, self.root).unwrap()
        try:
            mode = target.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            return Ok(ContentKind.absent)
        except OSError as exc:
            return Err([errors.AccessFailed(path=target, reason=str(exc)).with_cause(exc)])
        if stat.S_ISREG(mode):
            return Ok(ContentKind.file)
        if stat.S_ISDIR(mode):
            return Ok(ContentKind.directory)
        return Err([errors.AccessFailed(path=target, reason="Unsupported filesystem object.")])

    @unwrap_to_error
    def inspect_tree(self, path: ProjectPathId = ProjectPathId("@/")) -> Result[ContentTree]:
        """Inspect selected files without retaining their contents."""
        return self._inspect_tree(resolve_root_anchored_path(path, self.root).unwrap())

    @unwrap_to_error
    def _inspect_tree(self, path: Path) -> Result[ContentTree]:  # noqa: CCR001
        resolve_inside_project(path, self.root).unwrap()
        try:
            if not path.exists():
                return Ok(ContentTree(kind=ContentKind.absent))
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                return Ok(
                    ContentTree(
                        kind=ContentKind.file,
                        files=(
                            FileInfo(
                                path=ProjectPathId("@/"),
                                hash=self._hash_file(path),
                                executable=bool(mode & 0o111),
                            ),
                        ),
                    )
                )
            if not stat.S_ISDIR(mode):
                return Err([errors.AccessFailed(path=path, reason="Unsupported filesystem object.")])
            files: list[FileInfo] = []
            for child in sorted(path.iterdir()):
                subtree = self._inspect_tree(child).unwrap()
                for file in subtree.files:
                    suffix = file.path.removeprefix("@/")
                    files.append(
                        FileInfo(
                            path=ProjectPathId("@/" + child.name + ("/" + suffix if suffix else "")),
                            hash=file.hash,
                            executable=file.executable,
                        )
                    )
            return Ok(ContentTree(kind=ContentKind.directory, files=tuple(files)))
        except (OSError, UnicodeError, pydantic.ValidationError) as exc:
            return Err([errors.AccessFailed(path=path, reason=str(exc)).with_cause(exc)])

    def _hash_file(self, path: Path) -> ContentHash:
        with path.open("rb") as stream:
            return ContentHash("sha256:" + hashlib.file_digest(stream, "sha256").hexdigest())

    @unwrap_to_error
    def copy_file(self, path: ProjectPathId, destination: BinaryIO) -> Result[None]:
        """Stream a previously inspected regular file to a caller-owned output.

        The caller must inspect the source first and account for any intervening
        changes made by this invocation. Copying does not inspect its kind again.
        """
        source = resolve_root_anchored_path(path, self.root).unwrap()
        try:
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, destination)
        except OSError as exc:
            return Err([errors.AccessFailed(path=source, reason=str(exc)).with_cause(exc)])
        return Ok(None)

    @unwrap_to_error
    def write_stream(
        self, path: ProjectPathId, write: Callable[[BinaryIO], Result[None]], *, executable: bool = False
    ) -> Result[None]:
        """Write one selected file, creating its required parent directories."""
        self._require_writable(self.root / path.removeprefix("@/")).unwrap()
        target = resolve_root_anchored_path(path, self.root).unwrap()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as destination:
                result = write(destination)
            if result.is_err():
                return result
            if executable:
                target.chmod(target.stat().st_mode | 0o111)
        except OSError as exc:
            return Err([errors.AccessFailed(path=target, reason=str(exc)).with_cause(exc)])
        return Ok(None)
