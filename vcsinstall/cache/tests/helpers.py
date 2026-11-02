import subprocess  # noqa: S404 — Tests launch only controlled child processes.
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from selectors import EVENT_READ, DefaultSelector

from vcsinstall.cache import CacheRootPath, RepoCache
from vcsinstall.domain.tests import make


def cached_repository(root: Path, *, vcs: str = "git", repository: str = "https://host/shared") -> Path:
    with RepoCache(
        root=CacheRootPath(root), dependency=make.dependency(vcs=vcs, repository=repository)
    ).lock() as result:
        entry = result.unwrap()
        payload = entry / "repository" / "versions"
        payload.mkdir(parents=True)
        (payload / "first").write_bytes(b"cached first revision")
        (payload / "second").write_bytes(b"cached second revision")
    return entry


@contextmanager
def held_lock(path: Path) -> Iterator[None]:
    program = """
import sys
from filelock import FileLock
with FileLock(sys.argv[1], preserve_lock_file=True):
    print('ready', flush=True)
    sys.stdin.buffer.read()
"""
    with subprocess.Popen(  # noqa: S603 — Fixed interpreter and controlled test program, without a shell.
        [sys.executable, "-c", program, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:  # noqa: S603
        try:
            assert process.stdout is not None
            with DefaultSelector() as selector:
                selector.register(process.stdout, EVENT_READ)
                assert selector.select(timeout=5), "Lock holder did not start"
            assert process.stdout.readline() == b"ready\n"
            yield
        finally:
            process.communicate(timeout=5)
