"""Command-independent operation coordination."""

from vcsinstall.runtime.commands import (
    dependency_add,
    dependency_list,
    dependency_outdated,
    dependency_remove,
    entry_add,
    entry_remove,
)
from vcsinstall.runtime.updates import dependency_update

__all__ = [
    "dependency_add",
    "dependency_list",
    "dependency_outdated",
    "dependency_remove",
    "dependency_update",
    "entry_add",
    "entry_remove",
    "errors",
]
from vcsinstall.runtime import errors
