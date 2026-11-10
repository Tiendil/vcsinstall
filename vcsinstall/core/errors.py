from llm_tool_cli.core import errors as shared_errors


class InternalError(shared_errors.InternalError):
    """Root for internal vcsinstall exceptions."""


class EnvironmentError(shared_errors.EnvironmentError):
    """Root for expected vcsinstall failures returned as values."""
