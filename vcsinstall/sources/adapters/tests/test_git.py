import pytest

from vcsinstall.domain import SourceRevision
from vcsinstall.domain.tests import make
from vcsinstall.sources import errors
from vcsinstall.sources.adapters.git import GitSourceAdapter


class TestGitSourceAdapter:
    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"branch": "main"},
            {"tag": "Release+%2F"},
            {"tag": "HEAD"},
            {"rev": "1" * 40},
            {"rev": "a" * 64},
        ],
    )
    def test_validate_dependency__accepts_supported_selection(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(**fields)

        assert GitSourceAdapter().validate_dependency(dependency).is_ok()

    @pytest.mark.parametrize("field", ["branch", "tag"])
    @pytest.mark.parametrize(
        "value", ["a..b", "a@{b", "a b", "a~2", "a/b.lock", ".hidden", "/a", "a/", "a//b", "a.", "a\\b"]
    )
    def test_validate_dependency__rejects_invalid_native_refs(self, field: str, value: str) -> None:
        result = GitSourceAdapter().validate_dependency(make.dependency(**{field: value}))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.InvalidGitReference)
        assert error.field == field
        assert error.value == value

    @pytest.mark.parametrize("value", ["HEAD", "-branch"])
    def test_validate_dependency__rejects_special_branches(self, value: str) -> None:
        result = GitSourceAdapter().validate_dependency(make.dependency(branch=value))

        assert isinstance(result.unwrap_err()[0], errors.InvalidGitReference)

    @pytest.mark.parametrize("revision", ["HEAD", "main~2", "a" * 39, "A" * 40, "sha256:" + "a" * 64])
    def test_validate_dependency__rejects_noncomplete_revision(self, revision: str) -> None:
        error = GitSourceAdapter().validate_dependency(make.dependency(rev=revision)).unwrap_err()[0]

        assert isinstance(error, errors.InvalidRevision)
        assert error.field == "rev"
        assert error.value == revision

    @pytest.mark.parametrize("revision", ["1" * 40, "a" * 64])
    @pytest.mark.parametrize("field", ["branch", "tag", "rev"])
    def test_validate_lock__accepts_complete_revision(self, revision: str, field: str) -> None:
        dependency = make.dependency(**{field: revision if field == "rev" else "Release/One"})

        assert GitSourceAdapter().validate_lock(dependency, SourceRevision(revision)).is_ok()

    def test_validate_lock__requires_selection(self) -> None:
        result = GitSourceAdapter().validate_lock(make.dependency(), SourceRevision("1" * 40))

        assert isinstance(result.unwrap_err()[0], errors.MissingGitSelector)

    @pytest.mark.parametrize("revision", ["", "HEAD", "a" * 39, "A" * 40])
    def test_validate_lock__rejects_invalid_revision(self, revision: str) -> None:
        result = GitSourceAdapter().validate_lock(make.dependency(branch="main"), SourceRevision(revision))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.InvalidRevision)
        assert error.field == "revision"
        assert error.value == revision

    def test_validate_lock__checks_declaration(self) -> None:
        result = GitSourceAdapter().validate_lock(make.dependency(branch="bad..branch"), SourceRevision("1" * 40))

        assert isinstance(result.unwrap_err()[0], errors.InvalidGitReference)

    def test_validate_lock__rejects_different_pin(self) -> None:
        result = GitSourceAdapter().validate_lock(make.dependency(rev="1" * 40), SourceRevision("2" * 40))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.RevisionMismatch)
        assert error.expected == "1" * 40
        assert error.revision == "2" * 40
