"""Project operation exclusion, independent of disposable repository caches."""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import BaseFileLock, FileLock, Timeout
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config import locate_config, resolve_init_config_path
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import (
    PathInput,
    ProjectConfigPath,
    ProjectRootPath,
    resolve_inside_project,
    resolve_project_root,
)
from platformdirs import user_state_path

from vcsinstall.workspace import errors

LOCK_TIMEOUT_SECONDS = 10


@unwrap_to_error
def _resolve_project_lock_path(project_root: ProjectRootPath) -> Result[Path]:
    """Select the coordination path without creating directories or acquiring a lock."""
    try:
        state_directory = user_state_path("vcsinstall", appauthor=False, ensure_exists=False)
    except (OSError, RuntimeError) as exc:
        return Err([errors.ProjectLockAccessFailed(project_root=project_root, reason=str(exc)).with_cause(exc)])

    if not state_directory.is_absolute():
        return Err(
            [
                errors.ProjectLockAccessFailed(
                    project_root=project_root,
                    path=state_directory,
                    reason="The user state directory must be absolute.",
                )
            ]
        )
    state_root = resolve_project_root(state_directory).unwrap()
    identity = hashlib.sha256(os.fsencode(os.path.normcase(str(project_root)))).hexdigest()
    return Ok(resolve_inside_project(state_root / "projects" / identity / "operation.lock", state_root).unwrap())


def _acquire_project_lock(project_root: ProjectRootPath, path: Path) -> Result[BaseFileLock]:
    """Prepare and acquire the lock at an already resolved coordination path."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            mode = None
        if mode is not None and not stat.S_ISREG(mode):
            return Err(
                [
                    errors.ProjectLockAccessFailed(
                        project_root=project_root, path=path, reason="The coordination file must be a regular file."
                    )
                ]
            )

        lock = FileLock(
            path, timeout=LOCK_TIMEOUT_SECONDS, preserve_lock_file=True, fallback_to_soft=False, is_singleton=True
        )
        lock.acquire()
    except Timeout as exc:
        return Err(
            [
                errors.ProjectLockTimeout(
                    project_root=project_root, path=path, timeout=LOCK_TIMEOUT_SECONDS
                ).with_cause(exc)
            ]
        )
    except OSError as exc:
        return Err(
            [errors.ProjectLockAccessFailed(project_root=project_root, path=path, reason=str(exc)).with_cause(exc)]
        )
    return Ok(lock)


@contextmanager
def project_operation(
    *, cwd: PathInput, path: ProjectConfigPath | None = None, initialize: bool = False
) -> Iterator[Result[ProjectConfigPath]]:
    """Select a project and hold its operation lock without loading project files.

    Initialization selects the intended target without upward discovery. Other
    operations discover existing configuration or use the explicit path. All
    configuration filenames under the same resolved parent share this lock.
    """
    filename = settings.static_config.get().config_filename
    if filename is None:
        raise config_errors.ConfigFilenameNotSet()
    selected = (
        resolve_init_config_path(filename, path=path, cwd=cwd)
        if initialize
        else locate_config(filename, path=path, cwd=cwd)
    )
    if selected.is_err():
        yield selected
        return

    config_path = selected.unwrap()
    project_root = ProjectRootPath(config_path.parent)
    lock_path = _resolve_project_lock_path(project_root)
    if lock_path.is_err():
        yield Err(lock_path.unwrap_err())
        return

    acquired = _acquire_project_lock(project_root, lock_path.unwrap())
    if acquired.is_err():
        yield Err(acquired.unwrap_err())
        return

    lock = acquired.unwrap()
    try:
        yield Ok(config_path)
    finally:
        lock.release()
