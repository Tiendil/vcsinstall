from pathlib import Path

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.paths.errors import PathResolutionFailed
from pytest_mock import MockerFixture

from vcsinstall.domain import RepositoryLocator, VcsId
from vcsinstall.sources import get_adapter, parse_repository
from vcsinstall.sources.locators import normalize_local_repository


class TestParseRepository:
    @pytest.mark.parametrize(
        "locator,repository,field,value",
        [
            (
                "git+https://host/team/repo.git?x=y#branch=Main+fix%252F",
                "git+https://host/team/repo.git?x=y",
                "branch",
                "Main+fix%2F",
            ),
            ("git+ssh://user@host/team/repo#tag=Release+%252F", "git+ssh://user@host/team/repo", "tag", "Release+%2F"),
            ("https://host/team/repo#rev=abc123", "https://host/team/repo", "revision", "abc123"),
            ("git://host/team/repo#Main+%252F", "git://host/team/repo", "unresolved", "Main+%2F"),
            ("git+https://host/repo#branch=%20%09Main+fix%20", "git+https://host/repo", "branch", "Main+fix"),
            ("git+https://host/repo#tag=%20Release%20", "git+https://host/repo", "tag", "Release"),
            ("git+https://host/repo#rev=%20abc123%20", "git+https://host/repo", "revision", "abc123"),
            ("git+https://host/repo#%20Main%20", "git+https://host/repo", "unresolved", "Main"),
            ("user@host:team/repo#literal", "user@host:team/repo#literal", None, None),
            ("../repo#branch=literal", "../repo#branch=literal", None, None),
            ("file:///a%20b#tag=release", "file:///a%20b", "tag", "release"),
            ("https://host/team/repo", "https://host/team/repo", None, None),
        ],
    )
    def test_parses_repository_and_version_intent(
        self, locator: str, repository: str, field: str | None, value: str | None
    ) -> None:
        parsed = parse_repository(RepositoryLocator(locator)).unwrap()

        assert parsed.repository == repository
        if field is None:
            assert parsed.selector is None
        else:
            assert parsed.selector is not None
            assert (parsed.selector.kind.value, parsed.selector.value) == (field, value)

    @pytest.mark.parametrize("locator", ["", "https://[invalid"])
    def test_invalid_locators_return_shared_diagnostic(self, locator: str) -> None:
        assert parse_repository(RepositoryLocator(locator)).is_err(InvalidArguments)

    @pytest.mark.parametrize(
        "fragment",
        [
            "",
            "wat=x",
            "branch=",
            "branch=x&tag=y",
            "branch=x#tag=y",
            "branch=x=y",
            "branch=%20",
            "tag=%09",
            "rev=%0A",
            "%20",
        ],
    )
    def test_invalid_fragments_return_shared_diagnostic(self, fragment: str) -> None:
        parsed = parse_repository(RepositoryLocator("git+https://host/repo#" + fragment))

        assert parsed.is_err(InvalidArguments)

    @pytest.mark.parametrize("value,expected", [("%", "%"), ("%ff", "\ufffd")])
    def test_selector_decoding_uses_standard_defaults(self, value: str, expected: str) -> None:
        parsed = parse_repository(RepositoryLocator("git+https://host/repo#branch=" + value)).unwrap()

        assert parsed.selector is not None
        assert parsed.selector.value == expected


class TestNormalizeLocalRepository:
    def test_file_urls_decode_exactly_once_and_resolve_aliases(self, tmp_path: Path) -> None:
        source = tmp_path / "a%20+b#c"
        source.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(source, target_is_directory=True)
        for locator in (source.as_uri(), alias.as_uri(), source.as_uri().replace("file:///", "file://localhost/")):
            normalized = normalize_local_repository(RepositoryLocator(locator), cwd=tmp_path).unwrap()

            assert normalized == str(source)

    @pytest.mark.parametrize("vcs", [VcsId.git, VcsId.filesystem])
    def test_adapters_normalize_qualified_file_urls(self, tmp_path: Path, vcs: VcsId) -> None:
        source = tmp_path / "space % + #"
        source.mkdir()
        normalized = (
            get_adapter(vcs)
            .normalize_repository(RepositoryLocator(vcs.value + "+" + source.as_uri()), cwd=tmp_path)
            .unwrap()
        )

        assert normalized == str(source)

    @pytest.mark.parametrize(
        "locator", ["", "https://host/repo", "file://server/shared", "file:///shared?query=value", "file://[invalid"]
    )
    def test_unsupported_local_locators_are_non_matches(self, tmp_path: Path, locator: str) -> None:
        assert normalize_local_repository(RepositoryLocator(locator), cwd=tmp_path).unwrap() is None

    def test_home_expansion_failure_remains_operational(self, tmp_path: Path, mocker: MockerFixture) -> None:
        cause = PermissionError("Cannot expand the home directory")
        mocker.patch("vcsinstall.sources.locators.Path.expanduser", side_effect=cause)

        result = normalize_local_repository(RepositoryLocator("~/source"), cwd=tmp_path)

        problem = result.unwrap_err()[0]
        assert isinstance(problem, PathResolutionFailed)
        assert problem.cause is cause

    def test_unqualified_path_keeps_fragment_characters_literal(self, tmp_path: Path) -> None:
        normalized = normalize_local_repository(RepositoryLocator("../repo#branch=literal"), cwd=tmp_path).unwrap()

        assert normalized == str(tmp_path.parent / "repo#branch=literal")
