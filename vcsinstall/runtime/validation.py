"""Pure guards for dependency operations and local-content protection."""

from collections.abc import Mapping

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash, DependencyName, SourceRevision, VersionSelector
from vcsinstall.runtime import errors


def validate_redefinition(name: DependencyName, *, exists: bool, redefine: bool) -> Result[None]:
    """Require explicit permission to redefine an existing dependency name.

    Example: adding an already declared 'ci' fails with redefine=False and
    passes with redefine=True, even when the proposed definition is identical.
    """
    if exists and not redefine:
        return Err(
            [
                InvalidArguments(
                    reason=f"Dependency {name!r} is already declared.",
                    ways_to_fix=["Use --redefine to redefine the existing dependency."],
                )
            ]
        )
    return Ok(None)


def validate_entry_redefinition(destination: ProjectPathId, *, exists: bool, redefine: bool) -> Result[None]:
    """Require explicit permission to redefine an existing entry declaration.

    Example: an already declared '@/docs' fails with redefine=False and passes
    with redefine=True. An undeclared destination needs no such permission.
    """
    if exists and not redefine:
        return Err(
            [
                InvalidArguments(
                    reason=f"Destination {destination!r} is already declared.",
                    ways_to_fix=["Use --redefine to redefine the entry at this destination."],
                )
            ]
        )
    return Ok(None)


def content_matches(
    expected: Mapping[ProjectPathId, ContentHash],
    *,
    files: Mapping[ProjectPathId, ContentHash],
) -> bool:
    """Check every existing file against one expected state, allowing missing files.

    Example: expected {a: old, b: old} accepts actual {a: old}, but rejects
    {a: edited} and {a: old, extra: new}. An empty actual file set matches.
    """
    return all(expected.get(path) == digest for path, digest in files.items())


def validate_no_local_content_conflicts(
    previous: Mapping[ProjectPathId, ContentHash],
    proposed: Mapping[ProjectPathId, ContentHash],
    *,
    files: Mapping[ProjectPathId, ContentHash],
    destination: ProjectPathId,
) -> Result[None]:
    """Require all existing local files to match either the old or proposed state.

    Example: old {a: old, b: old} and proposed {a: new, b: new} accept actual
    {a: old} or {a: new, b: new}, but reject {a: old, b: new} and local additions.
    Missing files may be restored; independent old/new edits are not merged.
    """
    if not any(content_matches(expected, files=files) for expected in (previous, proposed)):
        return Err([errors.LocalConflict(destination=destination)])
    return Ok(None)


def validate_freshness(  # noqa: CFQ002 — Compare supplied selector and revision facts without source access.
    dependency: DependencyName,
    *,
    selector: VersionSelector | None,
    locked_revision: SourceRevision,
    current_revision: SourceRevision,
    allow_outdated: bool,
) -> Result[None]:
    """Reject differing locked and current revisions unless outdated use is permitted.

    Example: locked revision A and current revision B fail with
    allow_outdated=False and pass with True. Equal revisions pass either way.
    These are supplied facts; this guard neither refreshes nor advances a lock.
    """
    if locked_revision != current_revision and not allow_outdated:
        return Err(
            [
                errors.OutdatedDependency(
                    dependency=dependency,
                    selector=selector,
                    locked_revision=locked_revision,
                    current_revision=current_revision,
                )
            ]
        )
    return Ok(None)


def validate_removal_content(
    baseline: Mapping[ProjectPathId, ContentHash],
    *,
    files: Mapping[ProjectPathId, ContentHash],
    destination: ProjectPathId,
) -> Result[None]:
    """Require all remaining files to agree with the verified removal baseline.

    Example: baseline {a: old, b: old} accepts actual {a: old} despite missing
    b, but rejects {a: edited} and {a: old, extra: new}. Empty directories do
    not contribute files; baseline trust is established by the caller.
    """
    if not content_matches(baseline, files=files):
        return Err([errors.RemovalConflict(destination=destination)])
    return Ok(None)
