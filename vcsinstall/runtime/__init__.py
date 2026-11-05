"""Command-independent operation coordination."""

from vcsinstall.runtime.commands import (
    dependency_add,
    dependency_list,
    dependency_outdated,
    dependency_remove,
    entry_add,
    entry_list,
    entry_remove,
)
from vcsinstall.runtime.installations import install
from vcsinstall.runtime.publications import dependency_push
from vcsinstall.runtime.updates import dependency_update

__all__ = [
    "dependency_add",
    "dependency_list",
    "dependency_outdated",
    "dependency_remove",
    "dependency_update",
    "dependency_push",
    "entry_add",
    "entry_list",
    "entry_remove",
    "install",
    "errors",
]
from vcsinstall.runtime import errors
