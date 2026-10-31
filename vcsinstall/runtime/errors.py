from vcsinstall.core import errors as core_errors


class InternalError(core_errors.InternalError):
    pass


class EnvironmentError(core_errors.EnvironmentError):
    pass


class LocalConflict(EnvironmentError):
    code: str = "local_content_conflict"
    message: str = "Local content at {error.destination} differs from both its baseline and the selected source."
    ways_to_fix: list[str] = [
        "Preserve your local changes elsewhere or make the complete entry content match "
        "its baseline or selected source, then retry."
    ]
    destination: str


class InstallationWriteFailed(EnvironmentError):
    code: str = "installation_write_failed"
    message: str = (
        "Installing files failed; installed content may be inconsistent with the project records. "
        "The project lock has not advanced."
    )
    ways_to_fix: list[str] = ["Resolve the reported failure, inspect the installed entries, and retry."]
