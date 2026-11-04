"""Shared, effect-free contribution scope and option checks."""

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import ProjectPathId, contains_path, join_path

from vcsinstall.domain import ContentHash, ContentKind, SourcePath
from vcsinstall.hosting.entities import Contribution, DeliveryMode, PushOptions


def validate_push_options(  # noqa: CCR001 — Collect incompatible mode options before repository access.
    options: PushOptions, *, has_message: bool = False
) -> Result[None]:
    """Reject mode-inapplicable options before repository access.

    For example, patch requires a file path and rejects --branch; overwrite rejects messages.
    """
    mode = options.mode
    invalid = []
    if options.branch is not None and mode not in (DeliveryMode.pr, DeliveryMode.branch, DeliveryMode.direct):
        invalid.append("--branch")
    if options.base is not None and mode != DeliveryMode.pr:
        invalid.append("--base")
    if options.push_repository is not None and mode not in (DeliveryMode.pr, DeliveryMode.branch, DeliveryMode.direct):
        invalid.append("--push-repository")
    if options.patch_file is not None and mode != DeliveryMode.patch:
        invalid.append("--output")
    if mode == DeliveryMode.overwrite and has_message:
        invalid.append("--message")
    if invalid:
        return Err([InvalidArguments(reason=f"Mode {mode} does not accept {', '.join(invalid)}.")])
    if mode == DeliveryMode.patch and options.patch_file is None:
        return Err([InvalidArguments(reason="Patch mode requires --output PATH.")])
    return Ok(None)


def _scoped_hashes(change: Contribution, root: SourcePath) -> dict[ProjectPathId, ContentHash]:
    files = {join_path(change.source_path, path): digest for path, digest in change.content.file_hashes.items()}
    return {path: digest for path, digest in files.items() if contains_path(root, path)}


def validate_mappings(  # noqa: CCR001 — Compare complete scopes for each pair of overlapping mappings.
    changes: tuple[Contribution, ...],
) -> Result[None]:
    """Require overlapping complete mappings to propose the same content in their shared scope.

    For example, two mappings of '@/docs' with different guide hashes fail. A mapping of
    '@/docs/a' agrees with its enclosing '@/docs' only when both propose the same a content.
    """
    for index, change in enumerate(changes):
        for other in changes[:index]:
            if contains_path(change.source_path, other.source_path):
                parent, child = change, other
            elif contains_path(other.source_path, change.source_path):
                parent, child = other, change
            else:
                continue
            obstruction = (
                parent.source_path != child.source_path
                and parent.content.kind == ContentKind.file
                and bool(child.content.files)
            )
            if obstruction or _scoped_hashes(parent, child.source_path) != _scoped_hashes(child, child.source_path):
                return Err(
                    [
                        InvalidArguments(
                            reason=(
                                f"Conflicting contribution mappings at {parent.source_path} "
                                f"and {child.source_path}."
                            )
                        )
                    ]
                )
    return Ok(None)


def covering_changes(changes: tuple[Contribution, ...]) -> tuple[Contribution, ...]:
    """Keep one complete mapping for each compatible outer scope."""
    selected: list[Contribution] = []
    for change in sorted(changes, key=lambda item: (len(item.source_path), item.source_path, item.destination)):
        if not any(contains_path(parent.source_path, change.source_path) for parent in selected):
            selected.append(change)
    return tuple(selected)
