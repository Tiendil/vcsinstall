"""Shared native VCS execution and object inspection."""

from vcsinstall.vcs_clients import errors
from vcsinstall.vcs_clients.git import (
    GitClient,
    GitObjectId,
    GitObjectKind,
    GitTreeEntry,
    clone_mirror,
    is_git_object_id,
    is_git_object_id_input,
)

__all__ = [
    "GitClient",
    "GitObjectId",
    "GitObjectKind",
    "GitTreeEntry",
    "clone_mirror",
    "errors",
    "is_git_object_id",
    "is_git_object_id_input",
]
