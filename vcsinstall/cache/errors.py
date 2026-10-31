from pathlib import Path

from vcsinstall.core import errors as core_errors


class InternalError(core_errors.InternalError):
    pass


class EnvironmentError(core_errors.EnvironmentError):
    pass


class AccessFailed(EnvironmentError):
    code: str = "cache_access_failed"
    message: str = "Cannot access cache {error.path}: {error.reason}"
    ways_to_fix: list[str] = ["Provide --cache-dir DIRECTORY to select a usable cache directory."]
    path: Path
    reason: str


class LockTimeout(AccessFailed):
    code: str = "cache_lock_timeout"
    reason: str = "Timed out after 10 seconds waiting for cache.lock."
