import hashlib
import json
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import BaseFileLock, FileLock, Timeout
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import ProjectRootPath, resolve_inside_project

from vcsinstall.cache import errors
from vcsinstall.cache.entities import CacheRootPath
from vcsinstall.domain import Dependency

LOCK_TIMEOUT_SECONDS = 10


@unwrap_to_error
def _acquire_lock(  # noqa: CCR001 — Creation, absent cleanup roots, and lock failures share one access boundary.
    root: CacheRootPath, *, create: bool
) -> Result[BaseFileLock | None]:
    try:
        if create:
            root.mkdir(parents=True, exist_ok=True)
        else:
            try:
                mode = root.stat().st_mode
            except FileNotFoundError:
                return Ok(None)
            if not stat.S_ISDIR(mode):
                return Err([errors.AccessFailed(path=root, reason="Cache root must be a directory.")])

        path = resolve_inside_project(root / "cache.lock", ProjectRootPath(root)).unwrap()
        lock = FileLock(
            path, timeout=LOCK_TIMEOUT_SECONDS, preserve_lock_file=True, fallback_to_soft=False, is_singleton=True
        )
        lock.acquire()
    except Timeout as exc:
        return Err([errors.LockTimeout(path=root).with_cause(exc)])
    except OSError as exc:
        return Err([errors.AccessFailed(path=root, reason=str(exc)).with_cause(exc)])
    return Ok(lock)


@contextmanager
def _locked_cache(root: CacheRootPath, *, create: bool) -> Iterator[Result[CacheRootPath | None]]:
    opened = _acquire_lock(root, create=create)
    if opened.is_err():
        yield Err(opened.unwrap_err())
        return
    lock = opened.unwrap()
    try:
        yield Ok(root if lock is not None else None)
    finally:
        if lock is not None:
            lock.release()


def _repository_name(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _directories(path: Path) -> list[Path]:
    try:
        if not stat.S_ISDIR(path.lstat().st_mode):
            return []
    except FileNotFoundError:
        return []
    return [child for child in sorted(path.iterdir()) if stat.S_ISDIR(child.lstat().st_mode)]


def _owned_entry(entry: Path, vcs: str) -> bool:
    """Recognize the exact identity marker and digest written by RepoCache.

    A matching Git locator marker establishes ownership; a missing, symlinked,
    malformed, or differently named marker leaves the entry unmanaged.
    """
    marker = entry / "identity.json"
    try:
        if not stat.S_ISREG(marker.lstat().st_mode):
            return False
    except FileNotFoundError:
        return False

    try:
        identity = marker.read_text(encoding="utf-8")
        values = json.loads(identity)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(values, list)
        and len(values) == 2
        and values[0] == vcs
        and isinstance(values[1], str)
        and bool(values[1].strip())
        and identity == json.dumps(values, ensure_ascii=False)
        and entry.name == _repository_name(identity)
    )


def _remove_entry(entry: Path) -> None:
    # Keep ownership metadata until payload removal succeeds, so failed cleanup can be retried.
    for child in sorted(entry.iterdir()):
        if child.name == "identity.json":
            continue
        if stat.S_ISDIR(child.lstat().st_mode):
            shutil.rmtree(child)
        else:
            child.unlink()
    (entry / "identity.json").unlink()
    entry.rmdir()


@unwrap_to_error
def clean_cache(root: CacheRootPath) -> Result[bool]:
    """Remove owned repository entries under the shared lock, returning whether data was removed."""
    with _locked_cache(root, create=False) as access:
        if access.unwrap() is None:
            return Ok(False)
        try:
            entries = [
                entry
                for backend in _directories(root / "repos")
                for entry in _directories(backend)
                if _owned_entry(entry, backend.name)
            ]
        except OSError as exc:
            return Err([errors.AccessFailed(path=root, reason=str(exc)).with_cause(exc)])

        for entry in entries:
            try:
                _remove_entry(entry)
            except OSError as exc:
                return Err([errors.CleanupFailed(path=root, entry=entry, reason=str(exc)).with_cause(exc)])
        return Ok(bool(entries))


class RepoCache:
    """A repository entry and its exclusive access within one cache root."""

    __slots__ = ("root", "dependency")

    def __init__(self, *, root: CacheRootPath, dependency: Dependency) -> None:
        self.root = root
        self.dependency = dependency

    @contextmanager
    def lock(self) -> Iterator[Result[Path]]:
        """Prepare the repository entry and protect access until the scope ends."""
        with _locked_cache(self.root, create=True) as access:
            if access.is_err():
                yield Err(access.unwrap_err())
            else:
                yield self._entry()

    @unwrap_to_error
    def _entry(self) -> Result[Path]:
        try:
            identity = json.dumps([self.dependency.vcs, self.dependency.repository], ensure_ascii=False)
            name = _repository_name(identity)
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
