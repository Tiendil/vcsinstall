import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import BaseFileLock, FileLock, Timeout
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import ProjectRootPath, resolve_inside_project

from vcsinstall.cache import errors
from vcsinstall.cache.entities import CacheRootPath
from vcsinstall.domain import Dependency


class RepoCache:
    """A repository entry and its exclusive access within one cache root."""

    __slots__ = ("root", "dependency")

    def __init__(self, *, root: CacheRootPath, dependency: Dependency) -> None:
        self.root = root
        self.dependency = dependency

    @contextmanager
    def lock(self) -> Iterator[Result[Path]]:
        """Prepare the repository entry and protect access until the scope ends."""
        opened = self._acquire_lock()
        if opened.is_err():
            yield Err(opened.unwrap_err())
            return
        try:
            yield self._entry()
        finally:
            opened.unwrap().release()

    @unwrap_to_error
    def _acquire_lock(self) -> Result[BaseFileLock]:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = resolve_inside_project(self.root / "cache.lock", ProjectRootPath(self.root)).unwrap()
            lock = FileLock(path, timeout=10, preserve_lock_file=True, fallback_to_soft=False)
            lock.acquire()
        except Timeout as exc:
            return Err([errors.LockTimeout(path=self.root).with_cause(exc)])
        except OSError as exc:
            return Err([errors.AccessFailed(path=self.root, reason=str(exc)).with_cause(exc)])
        return Ok(lock)

    @unwrap_to_error
    def _entry(self) -> Result[Path]:
        try:
            identity = json.dumps([self.dependency.vcs, self.dependency.repository], ensure_ascii=False)
            name = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            entry = resolve_inside_project(
                self.root / "repos" / self.dependency.vcs / name, ProjectRootPath(self.root)
            ).unwrap()
            marker = entry / "identity.json"
            if entry.exists():
                if marker.is_symlink() or marker.read_text() != identity:
                    return Err(
                        [errors.AccessFailed(path=entry, reason="Conflicting cache ownership or repository identity.")]
                    )
            else:
                entry.mkdir(parents=True)
                marker.write_text(identity, encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return Err([errors.AccessFailed(path=self.root, reason=str(exc)).with_cause(exc)])
        return Ok(entry)
