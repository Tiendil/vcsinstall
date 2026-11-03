"""Lexical operations on canonical project-path identifiers."""

from llm_tool_cli.paths import ProjectPathId

__all__ = ["join_path", "contains_path"]

# TODO: llm_tool_cli.paths lacks lexical join and containment helpers. Move these
# functions there and remove this module when vcsinstall uses a release exporting them.


def join_path(root: ProjectPathId, relative: ProjectPathId) -> ProjectPathId:
    """Join canonical identifiers, treating '@/' as an empty relative path.

    For example, '@/docs' joined with '@/guide' becomes '@/docs/guide';
    joining '@/docs' with '@/' preserves '@/docs'. No normalization or I/O is needed.
    """
    suffix = relative.removeprefix("@/")
    return ProjectPathId(root.rstrip("/") + "/" + suffix) if suffix else root


def contains_path(root: ProjectPathId, path: ProjectPathId) -> bool:
    """Include a canonical root and its descendants, respecting segment boundaries.

    For example, '@/docs' includes '@/docs' and '@/docs/a', but not '@/docs-old'.
    '@/' includes every canonical identifier, including itself.
    """
    return root == "@/" or path == root or path.startswith(root + "/")
