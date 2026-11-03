import subprocess  # noqa: S404 — Tests launch controlled CLI subprocesses without a shell.
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from selectors import EVENT_READ, DefaultSelector

_COORDINATED_CLI = """
import sys
from vcsinstall.cli.application import main
from vcsinstall.workspace import coordination, loading

pause = sys.argv[1] == 'pause'
sys.argv = ['vcsinstall', *sys.argv[2:]]
acquire = coordination._acquire_project_lock
load = loading.load_workspace

def announce_acquire(root, path):
    print('acquiring', flush=True)
    return acquire(root, path)

def announce_load(**kwargs):
    result = load(**kwargs)
    print('loaded', flush=True)
    if pause:
        sys.stdin.buffer.read(1)
    return result

coordination._acquire_project_lock = announce_acquire
loading.load_workspace = announce_load
main()
"""


@contextmanager
def coordinated_cli(arguments: list[str], *, pause_after_loading: bool = False) -> Iterator[subprocess.Popen[bytes]]:
    """Observe real CLI acquisition and optionally pause after loading while its lock is held."""
    with subprocess.Popen(  # noqa: S603 — Fixed interpreter, test program and arguments, without a shell.
        [sys.executable, "-c", _COORDINATED_CLI, "pause" if pause_after_loading else "run", *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    ) as process:  # noqa: S603
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def wait_for_phase(process: subprocess.Popen[bytes], phase: bytes) -> None:
    assert process.stdout is not None
    with DefaultSelector() as selector:
        selector.register(process.stdout, EVENT_READ)
        assert selector.select(timeout=5), "CLI subprocess did not reach the expected phase"
    assert process.stdout.readline() == phase + b"\n"
