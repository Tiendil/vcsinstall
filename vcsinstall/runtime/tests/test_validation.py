import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash, DependencyName, SourceRevision
from vcsinstall.domain.tests import make
from vcsinstall.runtime import errors
from vcsinstall.runtime.validation import (
    validate_entry_redefinition,
    validate_freshness,
    validate_no_local_content_conflicts,
    validate_redefinition,
    validate_removal_content,
)


class TestValidateRedefinition:
    @pytest.mark.parametrize(
        "exists,redefine,valid", [(False, False, True), (False, True, True), (True, False, False), (True, True, True)]
    )
    def test_explicit_redefinition(self, exists: bool, redefine: bool, valid: bool) -> None:
        result = validate_redefinition(DependencyName("ci"), exists=exists, redefine=redefine)
        assert result.is_ok() == valid
        if not valid:
            assert isinstance(result.unwrap_err()[0], InvalidArguments)


class TestValidateEntryRedefinition:
    @pytest.mark.parametrize(
        "exists,redefine,valid",
        [
            (False, False, True),
            (False, True, True),
            (True, False, False),
            (True, True, True),
        ],
    )
    def test_explicit_redefinition_permission(self, exists: bool, redefine: bool, valid: bool) -> None:
        result = validate_entry_redefinition(ProjectPathId("@/installed"), exists=exists, redefine=redefine)

        assert result.is_ok() == valid
        if not valid:
            problem = result.unwrap_err()[0]
            assert isinstance(problem, InvalidArguments)
            assert "@/installed" in problem.reason
            assert "--redefine" in problem.ways_to_fix[0]


class TestValidateNoLocalContentConflicts:
    @pytest.mark.parametrize(
        "actual,valid",
        [
            ({}, True),
            ({"@/a": "old", "@/b": "old"}, True),
            ({"@/a": "new", "@/b": "new"}, True),
            ({"@/a": "old"}, True),
            ({"@/b": "new"}, True),
            ({"@/a": "old", "@/b": "new"}, False),
            ({"@/a": "local"}, False),
            ({"@/extra": "old"}, False),
            ({"@/": "obstruction"}, False),
        ],
    )
    def test_complete_existing_content_scope(self, actual: dict[str, str], valid: bool) -> None:
        previous = {ProjectPathId(path): ContentHash("old") for path in ("@/a", "@/b")}
        proposed = {ProjectPathId(path): ContentHash("new") for path in ("@/a", "@/b")}
        result = validate_no_local_content_conflicts(
            previous,
            proposed,
            files={ProjectPathId(path): ContentHash(value) for path, value in actual.items()},
            destination=ProjectPathId("@/installed"),
        )
        assert result.is_ok() == valid
        if not valid:
            assert isinstance(result.unwrap_err()[0], errors.LocalConflict)


class TestValidateFreshness:
    @pytest.mark.parametrize("current", ["locked", "advanced", "rewound"])
    @pytest.mark.parametrize("allow_outdated", [False, True])
    def test_compares_complete_revisions_without_advancing_them(self, current: str, allow_outdated: bool) -> None:
        selector = make.selector("branch", "trunk")
        result = validate_freshness(
            DependencyName("ci"),
            selector=selector,
            locked_revision=SourceRevision("locked"),
            current_revision=SourceRevision(current),
            allow_outdated=allow_outdated,
        )

        assert result.is_ok() == (current == "locked" or allow_outdated)
        if result.is_err():
            problem = result.unwrap_err()[0]
            assert isinstance(problem, errors.OutdatedDependency)
            assert problem.dependency == "ci"
            assert problem.selector == selector
            assert problem.locked_revision == "locked"
            assert problem.current_revision == current


class TestValidateRemovalContent:
    @pytest.mark.parametrize(
        "actual,valid",
        [
            ({}, True),
            ({"@/a": "old", "@/b": "old"}, True),
            ({"@/a": "old"}, True),
            ({"@/a": "edited"}, False),
            ({"@/extra": "old"}, False),
            ({"@/": "old"}, False),
        ],
    )
    def test_requires_every_existing_file_to_match_baseline(self, actual: dict[str, str], valid: bool) -> None:
        destination = ProjectPathId("@/installed")
        result = validate_removal_content(
            {ProjectPathId(path): ContentHash("old") for path in ("@/a", "@/b")},
            files={ProjectPathId(path): ContentHash(value) for path, value in actual.items()},
            destination=destination,
        )

        assert result.is_ok() == valid
        if result.is_err():
            problem = result.unwrap_err()[0]
            assert isinstance(problem, errors.RemovalConflict)
            assert problem.destination == destination
