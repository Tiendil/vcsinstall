import pytest

from vcsinstall.domain import SourceRevision, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.sources import errors
from vcsinstall.sources.adapters.noop import NoopSourceAdapter


class TestNoopSourceAdapter:
    @pytest.mark.parametrize(
        "fields",
        [{}, {"branch": "fixture branch"}, {"tag": "fixture..tag"}, {"rev": "opaque-revision"}],
    )
    def test_validate_dependency__accepts_opaque_selection(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(vcs=VcsId.noop, repository="fixture-id", **fields)

        assert NoopSourceAdapter().validate_dependency(dependency).is_ok()

    @pytest.mark.parametrize(
        "fields",
        [{}, {"branch": "fixture branch"}, {"tag": "fixture..tag"}, {"rev": "opaque-revision"}],
    )
    def test_validate_lock__accepts_opaque_revision(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(vcs=VcsId.noop, repository="fixture-id", **fields)

        assert NoopSourceAdapter().validate_lock(dependency, SourceRevision("opaque-revision")).is_ok()

    def test_validate_lock__rejects_empty_revision(self) -> None:
        result = NoopSourceAdapter().validate_lock(make.dependency(vcs=VcsId.noop), SourceRevision(""))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.InvalidRevision)
        assert error.vcs == VcsId.noop
        assert error.field == "revision"
        assert error.value == ""

    def test_validate_lock__rejects_mismatching_pin(self) -> None:
        dependency = make.dependency(vcs=VcsId.noop, rev="first-revision")

        result = NoopSourceAdapter().validate_lock(dependency, SourceRevision("second-revision"))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.RevisionMismatch)
        assert error.revision == "second-revision"
        assert error.expected == "first-revision"
