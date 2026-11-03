import io
from pathlib import Path

import pytest
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import RepositoryLocator, SourcePath, SourceRevision, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.sources import ResolvedSource, errors
from vcsinstall.sources.adapters.noop import NoopSourceAdapter


class TestNoopSourceAdapter:
    @pytest.fixture
    def source(self, tmp_path: Path) -> ResolvedSource:
        return ResolvedSource(
            dependency=make.dependency(vcs=VcsId.noop, repository="fixture-id"),
            path=tmp_path,
            revision=SourceRevision("fixture-revision"),
        )

    def test_resolve__requires_controlled_fixture(self, source: ResolvedSource) -> None:
        result = NoopSourceAdapter().resolve(source.dependency, source.path)

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.MissingNoopFixture)
        assert error.repository == source.dependency.repository

    def test_inspect__requires_controlled_fixture(self, source: ResolvedSource) -> None:
        result = NoopSourceAdapter().inspect(source, (SourcePath(ProjectPathId("@/guide")),))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.MissingNoopFixture)
        assert error.repository == source.dependency.repository

    def test_copy_file__requires_controlled_fixture(self, source: ResolvedSource) -> None:
        output = io.BytesIO()

        result = NoopSourceAdapter().copy_file(source, SourcePath(ProjectPathId("@/guide")), output)

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.MissingNoopFixture)
        assert error.repository == source.dependency.repository
        assert not output.getvalue()

    def test_validate_input__accepts_unresolved_opaque_selection(self) -> None:
        adapter = NoopSourceAdapter()
        dependency = make.dependency(vcs=VcsId.noop, selector=make.selector("unresolved", "fixture reference"))

        assert adapter.validate_input(dependency).is_ok()
        assert adapter.validate_dependency(dependency).is_err(errors.UnsupportedNoopSelector)

    def test_normalize_repository__empty_fixture_identity_is_a_non_match(self, tmp_path: Path) -> None:
        assert NoopSourceAdapter().normalize_repository(RepositoryLocator(""), cwd=tmp_path).unwrap() is None

    def test_normalize_repository__preserves_opaque_fixture_notation(self, tmp_path: Path) -> None:
        repository = RepositoryLocator("opaque://[fixture value")

        assert NoopSourceAdapter().normalize_repository(repository, cwd=tmp_path).unwrap() == repository

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"selector": {"kind": "branch", "value": "fixture branch"}},
            {"selector": {"kind": "tag", "value": "fixture..tag"}},
            {"selector": {"kind": "revision", "value": "opaque-revision"}},
        ],
    )
    def test_validate_dependency__accepts_opaque_selection(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(vcs=VcsId.noop, repository="fixture-id", **fields)

        assert NoopSourceAdapter().validate_dependency(dependency).is_ok()

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"selector": {"kind": "branch", "value": "fixture branch"}},
            {"selector": {"kind": "tag", "value": "fixture..tag"}},
            {"selector": {"kind": "revision", "value": "opaque-revision"}},
        ],
    )
    def test_validate_revision_lock__accepts_opaque_revision(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(vcs=VcsId.noop, repository="fixture-id", **fields)

        assert NoopSourceAdapter().validate_revision_lock(dependency, SourceRevision("opaque-revision")).is_ok()

    def test_validate_revision_lock__rejects_empty_revision(self) -> None:
        result = NoopSourceAdapter().validate_revision_lock(make.dependency(vcs=VcsId.noop), SourceRevision(""))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.InvalidNoopRevision)
        assert error.vcs == VcsId.noop
        assert error.field == "revision"
        assert error.value == ""

    def test_validate_revision_lock__rejects_mismatching_pin(self) -> None:
        dependency = make.dependency(vcs=VcsId.noop, selector=make.selector("revision", "first-revision"))

        result = NoopSourceAdapter().validate_revision_lock(dependency, SourceRevision("second-revision"))

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.RevisionMismatch)
        assert error.revision == "second-revision"
        assert error.expected == "first-revision"
