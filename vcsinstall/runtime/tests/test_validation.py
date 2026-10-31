import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash, DependencyName
from vcsinstall.runtime import errors
from vcsinstall.runtime.validation import validate_no_local_content_conflicts, validate_redefinition


class TestValidateRedefinition:
    @pytest.mark.parametrize(
        "exists,redefine,valid", [(False, False, True), (False, True, True), (True, False, False), (True, True, True)]
    )
    def test_explicit_redefinition(self, exists: bool, redefine: bool, valid: bool) -> None:
        result = validate_redefinition(DependencyName("ci"), exists=exists, redefine=redefine)
        assert result.is_ok() == valid
        if not valid:
            assert isinstance(result.unwrap_err()[0], InvalidArguments)


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
