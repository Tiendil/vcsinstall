"""Pure guards for dependency operations and local-content protection."""

from collections.abc import Mapping

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash, DependencyName, SourceRevision, VersionSelector
from vcsinstall.runtime import errors
from vcsinstall.runtime.entities import LocalContentPolicy, LocalStatus


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


def local_content_status(
    expected: Mapping[ProjectPathId, ContentHash],
    *,
    files: Mapping[ProjectPathId, ContentHash],
) -> LocalStatus:
    """Compare complete local file identities with a matching baseline.

    Expected {a: old, b: old} and actual {a: old} are missing; actual
    {a: edited} is modified despite the missing b. Equal empty file sets
    are unmodified, including absent containers and empty directories.
    """
    if not content_matches(expected, files=files):
        return LocalStatus.modified
    if len(files) < len(expected):
        return LocalStatus.missing
    return LocalStatus.unmodified


def validate_no_local_content_conflicts(
    previous: Mapping[ProjectPathId, ContentHash],
    proposed: Mapping[ProjectPathId, ContentHash],
    *,
    files: Mapping[ProjectPathId, ContentHash],
    destination: ProjectPathId,
    policy: LocalContentPolicy,
) -> Result[None]:
    """Protect local content under the operation's installation or update policy.

    Protect accepts old {a: old, b: old} or the complete proposed state, but
    rejects actual {a: old} as a local deletion. Overwrite accepts any local
    content; callers still validate baselines, sources, and paths.
    """
    if policy == LocalContentPolicy.overwrite:
        return Ok(None)

    if files != previous and files != proposed:
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
