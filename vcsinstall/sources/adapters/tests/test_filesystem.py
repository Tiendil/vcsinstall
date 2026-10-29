import pytest

from vcsinstall.domain import SourceRevision
from vcsinstall.domain.tests import make
from vcsinstall.sources import errors
from vcsinstall.sources.adapters.filesystem import FilesystemSourceAdapter

_REVISION = SourceRevision("sha256:" + "a" * 64)


class TestFilesystemSourceAdapter:
    @pytest.mark.parametrize("fields", [{}, {"rev": _REVISION}])
    def test_validate_dependency__accepts_live_or_pinned(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(vcs="filesystem", repository="../does-not-exist", **fields)

        assert FilesystemSourceAdapter().validate_dependency(dependency).is_ok()

    @pytest.mark.parametrize("field", ["branch", "tag"])
    def test_validate_dependency__rejects_named_selection(self, field: str) -> None:
        dependency = make.dependency(vcs="filesystem", **{field: "main"})
        error = FilesystemSourceAdapter().validate_dependency(dependency).unwrap_err()[0]

        assert isinstance(error, errors.UnsupportedSelector)
        assert error.field == field

    @pytest.mark.parametrize("revision", ["a" * 64, "sha256:" + "A" * 64, "sha256:" + "a" * 63])
    def test_validate_dependency__rejects_invalid_fingerprint(self, revision: str) -> None:
        error = (
            FilesystemSourceAdapter()
            .validate_dependency(make.dependency(vcs="filesystem", rev=revision))
            .unwrap_err()[0]
        )

        assert isinstance(error, errors.InvalidRevision)
        assert error.field == "rev"
        assert error.value == revision

    @pytest.mark.parametrize("fields", [{}, {"rev": _REVISION}])
    def test_validate_lock__accepts_live_or_matching_pin(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(vcs="filesystem", **fields)

        assert FilesystemSourceAdapter().validate_lock(dependency, _REVISION).is_ok()

    def test_validate_lock__rejects_mismatching_pin(self) -> None:
        dependency = make.dependency(vcs="filesystem", rev=_REVISION)
        result = FilesystemSourceAdapter().validate_lock(dependency, SourceRevision("sha256:" + "b" * 64))

        assert isinstance(result.unwrap_err()[0], errors.RevisionMismatch)

    @pytest.mark.parametrize("revision", ["", "a" * 64, "sha256:" + "A" * 64])
    def test_validate_lock__rejects_invalid_fingerprint(self, revision: str) -> None:
        result = FilesystemSourceAdapter().validate_lock(make.dependency(vcs="filesystem"), SourceRevision(revision))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.InvalidRevision)
        assert error.field == "revision"

    def test_validate_lock__checks_declaration(self) -> None:
        result = FilesystemSourceAdapter().validate_lock(make.dependency(vcs="filesystem", tag="v1"), _REVISION)

        assert isinstance(result.unwrap_err()[0], errors.UnsupportedSelector)
