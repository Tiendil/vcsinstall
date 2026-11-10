import io
from pathlib import Path

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err
from llm_tool_cli.paths import ProjectPathId
from llm_tool_cli.paths.errors import PathResolutionFailed
from pytest_mock import MockerFixture

from vcsinstall.domain import ContentKind, RepositoryLocator, SourcePath, SourceRevision
from vcsinstall.domain.tests import make
from vcsinstall.filesystem import FSClient, hashes
from vcsinstall.sources import ResolvedSource, errors
from vcsinstall.sources.adapters.filesystem import FilesystemSourceAdapter

_REVISION = SourceRevision("sha256:" + "a" * 64)


class TestFilesystemSourceAdapter:
    def test_validate_input__accepts_an_unresolved_fingerprint(self) -> None:
        adapter = FilesystemSourceAdapter()
        dependency = make.dependency(vcs="filesystem", selector=make.selector("unresolved", _REVISION))

        assert adapter.validate_input(dependency).is_ok()
        assert adapter.validate_dependency(dependency).is_err(errors.UnsupportedFilesystemSelector)

    @pytest.mark.parametrize(
        "kind,value", [("unresolved", "invalid"), ("revision", "abc123"), ("branch", "main"), ("tag", "release")]
    )
    def test_validate_input__rejects_unsupported_selections(self, kind: str, value: str) -> None:
        dependency = make.dependency(vcs="filesystem", selector=make.selector(kind, value))

        assert FilesystemSourceAdapter().validate_input(dependency).is_err(InvalidArguments)

    @pytest.mark.parametrize("prefix", ["", "filesystem+"])
    def test_probe_repository__recognizes_file_urls(self, tmp_path: Path, prefix: str) -> None:
        locator = RepositoryLocator(prefix + (tmp_path / "missing-source").as_uri())

        assert FilesystemSourceAdapter().probe_repository(locator, cwd=tmp_path).unwrap()

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "file://[invalid",
            "https://host/source",
            "file://server/shared",
            "file:///source?query=value",
            "git+file:///source",
        ],
    )
    def test_normalize_repository__format_rejection_is_a_non_match(self, tmp_path: Path, value: str) -> None:
        adapter = FilesystemSourceAdapter()
        repository = RepositoryLocator(value)

        assert adapter.normalize_repository(repository, cwd=tmp_path).unwrap() is None
        assert not adapter.probe_repository(repository, cwd=tmp_path).unwrap()

    def test_normalize_repository__retains_operational_errors(self, tmp_path: Path, mocker: MockerFixture) -> None:
        problem = PathResolutionFailed(path="source", reason="Permission denied")
        mocker.patch("vcsinstall.sources.locators.resolve_project_root", return_value=Err([problem]))
        adapter = FilesystemSourceAdapter()
        repository = RepositoryLocator("source")

        assert adapter.normalize_repository(repository, cwd=tmp_path).unwrap_err() == [problem]
        assert adapter.probe_repository(repository, cwd=tmp_path).unwrap_err() == [problem]

    def test_normalize_repository__internal_failures_propagate(self, tmp_path: Path, mocker: MockerFixture) -> None:
        cause = errors.InternalError("Broken path normalization")
        mocker.patch("vcsinstall.sources.locators.resolve_project_root", side_effect=cause)
        adapter = FilesystemSourceAdapter()
        repository = RepositoryLocator("source")

        with pytest.raises(errors.InternalError) as normalized:
            adapter.normalize_repository(repository, cwd=tmp_path)
        with pytest.raises(errors.InternalError) as probed:
            adapter.probe_repository(repository, cwd=tmp_path)
        assert normalized.value is cause
        assert probed.value is cause

    @pytest.fixture
    def source(self, tmp_path: Path) -> ResolvedSource:
        root = tmp_path / "source"
        root.mkdir()
        (root / "nested").mkdir()
        (root / "nested" / "file").write_bytes(b"literal\x00bytes\r\n")
        (root / "other").write_bytes(b"other")
        (root / "empty").mkdir()
        dependency = make.dependency(vcs="filesystem", repository=str(root))
        return FilesystemSourceAdapter().resolve(dependency, root).unwrap()

    def test_inspect__inspects_all_requested_paths_in_one_source_scan(
        self, source: ResolvedSource, mocker: MockerFixture
    ) -> None:
        adapter = FilesystemSourceAdapter()
        inspect = mocker.spy(FSClient, "inspect_tree")
        paths = tuple(
            SourcePath(ProjectPathId(path)) for path in ("@/nested", "@/nested/file", "@/empty", "@/missing")
        )

        trees = adapter.inspect(source, paths).unwrap()

        assert inspect.call_count == 1
        directory, file, empty, absent = (trees[path] for path in paths)
        assert directory.kind == ContentKind.directory
        assert directory.file_hashes == {"@/file": hashes.hash_file(b"literal\x00bytes\r\n")}
        assert file.kind == ContentKind.file
        assert file.file_hashes == {"@/": hashes.hash_file(b"literal\x00bytes\r\n")}
        assert empty.kind == ContentKind.directory
        assert not empty.files
        assert absent.kind == ContentKind.absent
        assert not absent.files

    def test_resolve__resolves_an_untyped_fingerprint(self, source: ResolvedSource) -> None:
        dependency = source.dependency.replace(selector=make.selector("unresolved", source.revision))

        resolved = FilesystemSourceAdapter().resolve(dependency, source.path).unwrap()

        assert resolved.dependency.selector == make.selector("revision", source.revision)
        assert resolved.revision == source.revision

    def test_inspect__rejects_locked_revision_unavailable_in_live_source(self, source: ResolvedSource) -> None:
        adapter = FilesystemSourceAdapter()
        revision = SourceRevision(
            hashes.hash_files(
                {
                    ProjectPathId("@/nested/file"): hashes.hash_file(b"literal\x00bytes\r\n"),
                    ProjectPathId("@/other"): hashes.hash_file(b"old content outside the selection"),
                }
            )
        )

        locked = source.replace(revision=revision)

        problem = adapter.inspect(locked, (SourcePath(ProjectPathId("@/nested/file")),)).unwrap_err()[0]

        assert isinstance(problem, errors.RevisionMismatch)
        assert problem.expected == revision  # type: ignore[comparison-overlap]

    def test_copy_file__copies_inspected_repository_relative_paths(self, source: ResolvedSource) -> None:
        (source.path / "second").mkdir()
        (source.path / "second" / "file").write_bytes(b"second source")
        adapter = FilesystemSourceAdapter()
        source = adapter.resolve(source.dependency, source.path).unwrap()
        paths = tuple(SourcePath(ProjectPathId(path)) for path in ("@/nested", "@/second", "@/nested/file"))
        adapter.inspect(source, paths).unwrap()
        files = ("@/nested/file", "@/second/file", "@/nested/file")

        for file, expected in zip(
            files, (b"literal\x00bytes\r\n", b"second source", b"literal\x00bytes\r\n"), strict=True
        ):
            destination = io.BytesIO()
            adapter.copy_file(source, SourcePath(ProjectPathId(file)), destination).unwrap()
            assert destination.getvalue() == expected

    def test_copy_file__rejects_invalid_revision_before_reading(self, source: ResolvedSource) -> None:
        output = io.BytesIO()

        result = FilesystemSourceAdapter().copy_file(
            source.replace(revision=SourceRevision("invalid")), SourcePath(ProjectPathId("@/other")), output
        )

        assert result.is_err(errors.InvalidFilesystemRevision)
        assert not output.getvalue()

    @pytest.mark.parametrize("fields", [{}, {"selector": {"kind": "revision", "value": _REVISION}}])
    def test_validate_dependency__accepts_live_or_pinned(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(vcs="filesystem", repository="../does-not-exist", **fields)

        assert FilesystemSourceAdapter().validate_dependency(dependency).is_ok()

    @pytest.mark.parametrize("field", ["branch", "tag"])
    def test_validate_dependency__rejects_named_selection(self, field: str) -> None:
        dependency = make.dependency(vcs="filesystem", selector=make.selector(field, "main"))
        error = FilesystemSourceAdapter().validate_dependency(dependency).unwrap_err()[0]

        assert isinstance(error, errors.UnsupportedFilesystemSelector)
        assert error.field == field

    @pytest.mark.parametrize("revision", ["a" * 64, "sha256:" + "A" * 64, "sha256:" + "a" * 63])
    def test_validate_dependency__rejects_invalid_fingerprint(self, revision: str) -> None:
        error = (
            FilesystemSourceAdapter()
            .validate_dependency(make.dependency(vcs="filesystem", selector=make.selector("revision", revision)))
            .unwrap_err()[0]
        )

        assert isinstance(error, errors.InvalidFilesystemRevision)
        assert error.field == "rev"
        assert error.value == revision

    @pytest.mark.parametrize("fields", [{}, {"selector": {"kind": "revision", "value": _REVISION}}])
    def test_validate_revision_lock__accepts_live_or_matching_pin(self, fields: dict[str, object]) -> None:
        dependency = make.dependency(vcs="filesystem", **fields)

        assert FilesystemSourceAdapter().validate_revision_lock(dependency, _REVISION).is_ok()

    def test_validate_revision_lock__rejects_mismatching_pin(self) -> None:
        dependency = make.dependency(vcs="filesystem", selector=make.selector("revision", _REVISION))
        result = FilesystemSourceAdapter().validate_revision_lock(dependency, SourceRevision("sha256:" + "b" * 64))

        assert isinstance(result.unwrap_err()[0], errors.RevisionMismatch)

    @pytest.mark.parametrize("revision", ["", "a" * 64, "sha256:" + "A" * 64])
    def test_validate_revision_lock__rejects_invalid_fingerprint(self, revision: str) -> None:
        result = FilesystemSourceAdapter().validate_revision_lock(
            make.dependency(vcs="filesystem"), SourceRevision(revision)
        )

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.InvalidFilesystemRevision)
        assert error.field == "revision"

    def test_validate_revision_lock__checks_declaration(self) -> None:
        result = FilesystemSourceAdapter().validate_revision_lock(
            make.dependency(vcs="filesystem", selector=make.selector("tag", "v1")), _REVISION
        )

        assert isinstance(result.unwrap_err()[0], errors.UnsupportedFilesystemSelector)
