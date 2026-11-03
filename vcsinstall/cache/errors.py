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
    ways_to_fix: list[str] = [
        "Wait for the vcsinstall operation using this cache to finish, then retry.",
        "Use --cache-dir DIRECTORY to select a separate cache if the other operation must continue.",
    ]


class CleanupFailed(AccessFailed):
    code: str = "cache_cleanup_failed"
    message: str = (
        "Cannot finish cleaning cache {error.path} at {error.entry}: {error.reason}. "
        "Cached data may already have been partially removed."
    )
    ways_to_fix: list[str] = ["Resolve the reported filesystem failure and retry cache clean."]
    entry: Path
