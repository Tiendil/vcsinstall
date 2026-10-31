import tomli_w
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.core.result import Ok, Result, unwrap_to_error
from llm_tool_cli.paths import project_path_id_from_filesystem

from vcsinstall.filesystem import FSClient
from vcsinstall.filesystem import errors as filesystem_errors
from vcsinstall.workspace.config import Config
from vcsinstall.workspace.loading import Workspace
from vcsinstall.workspace.lockfile import Lockfile


class WorkspaceFileUpdates(BaseEntity):
    config: bytes | None
    lockfile: bytes | None


def serialize_workspace_changes(workspace: Workspace, config: Config, lockfile: Lockfile) -> WorkspaceFileUpdates:
    """Serialize changed models without filesystem access or path validation."""
    config_bytes = (
        tomli_w.dumps(config.model_dump(mode="json", exclude_none=True)).encode("utf-8")
        if config != workspace.config
        else None
    )
    lockfile_bytes = (
        tomli_w.dumps(lockfile.model_dump(mode="json", exclude_none=True)).encode("utf-8")
        if lockfile != workspace.lockfile
        else None
    )
    return WorkspaceFileUpdates(config=config_bytes, lockfile=lockfile_bytes)


@unwrap_to_error
def write_workspace_files(workspace: Workspace, serialized_config_and_lockfile: WorkspaceFileUpdates) -> Result[None]:
    client = FSClient(root=workspace.project_root, readonly=False)
    completed: list[str] = []
    updates = (
        (path, data)
        for path, data in (
            (workspace.config_path, serialized_config_and_lockfile.config),
            (workspace.lockfile_path, serialized_config_and_lockfile.lockfile),
        )
        if data is not None
    )
    for path, data in updates:
        identifier = project_path_id_from_filesystem(path, workspace.project_root).unwrap()
        result = client.write_file(identifier, data)
        if result.is_err():
            return result.map_err(
                lambda problems: [
                    (
                        problem.replace(
                            reason=f"{problem.reason}; completed project writes: {completed!r}. "
                            "Installed file changes may already have completed."
                        )
                        if isinstance(problem, filesystem_errors.AccessFailed)
                        else problem
                    )
                    for problem in problems
                ]
            )
        completed.append(str(path))
    return Ok(None)
