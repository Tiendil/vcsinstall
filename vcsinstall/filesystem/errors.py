from pathlib import Path

from llm_tool_cli.paths import ProjectRootPath

from vcsinstall.core import errors as core_errors


class InternalError(core_errors.InternalError):
    pass


class EnvironmentError(core_errors.EnvironmentError):
    pass


class AccessFailed(EnvironmentError):
    code: str = "filesystem_access_failed"
    message: str = "Cannot access {error.path}: {error.reason}"
    ways_to_fix: list[str] = ["Check the path, access permissions, and reported filesystem problem."]
    path: Path
    reason: str


class ReadOnly(EnvironmentError):
    code: str = "filesystem_readonly"
    message: str = "Filesystem client for {error.root} is read-only; cannot modify {error.path}."
    ways_to_fix: list[str] = ["Use a writable filesystem client for an explicitly authorized destination."]
    root: ProjectRootPath
    path: Path
