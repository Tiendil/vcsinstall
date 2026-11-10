"""Select project configuration and retain its shared project-lock scope."""

from collections.abc import Iterator
from contextlib import contextmanager

from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config import locate_config, resolve_init_config_path
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import PathInput, ProjectConfigPath, ProjectRootPath
from llm_tool_cli.tool_state.project_locks import project_lock

LOCK_TIMEOUT_SECONDS = 10


@contextmanager
def project_operation(
    *, cwd: PathInput, path: ProjectConfigPath | None = None, initialize: bool = False
) -> Iterator[Result[ProjectConfigPath]]:
    """Select configuration and hold its project lock without loading project files.

    Initialization selects the intended target without upward discovery. Other
    operations discover existing configuration or use the explicit path. All
    configuration filenames under the same resolved parent share this lock.
    """
    static = settings.static_config.get()
    filename = static.config_filename
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
    with project_lock(static.tool_name, ProjectRootPath(config_path.parent), timeout=LOCK_TIMEOUT_SECONDS) as locked:
        yield Err(locked.unwrap_err()) if locked.is_err() else Ok(config_path)
