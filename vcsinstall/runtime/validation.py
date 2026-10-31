"""Pure guards for dependency operations and local-content protection."""

from collections.abc import Mapping

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash, DependencyName
from vcsinstall.runtime import errors


def validate_redefinition(name: DependencyName, *, exists: bool, redefine: bool) -> Result[None]:
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


def content_matches(
    expected: Mapping[ProjectPathId, ContentHash],
    *,
    files: Mapping[ProjectPathId, ContentHash],
) -> bool:
    return all(expected.get(path) == digest for path, digest in files.items())


def validate_no_local_content_conflicts(
    previous: Mapping[ProjectPathId, ContentHash],
    proposed: Mapping[ProjectPathId, ContentHash],
    *,
    files: Mapping[ProjectPathId, ContentHash],
    destination: ProjectPathId,
) -> Result[None]:
    if not any(content_matches(expected, files=files) for expected in (previous, proposed)):
        return Err([errors.LocalConflict(destination=destination)])
    return Ok(None)
