import os
from pathlib import Path

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err
from llm_tool_cli.paths import ProjectPathId
from llm_tool_cli.paths.errors import PathResolutionFailed
from pytest_mock import MockerFixture

from vcsinstall.domain import ContentKind, RepositoryLocator, SourcePath, SourceRevision
from vcsinstall.domain.tests import make
from vcsinstall.filesystem import hashes
from vcsinstall.sources import ResolvedSource, errors
from vcsinstall.sources.adapters.git import GitSourceAdapter
from vcsinstall.vcs_clients import GitClient
from vcsinstall.vcs_clients.tests.helpers import create_repository


class TestGitSourceAdapter:
    @pytest.mark.parametrize(
        "locator",
        [
            "git+https://host/repository",
            "git+ssh://host/repository",
            "git+file:///missing-repository",
            "git://host/repository",
            "user@host:repository",
        ],
    )
    def test_probe_repository__recognizes_backend_notation(self, tmp_path: Path, locator: str) -> None:
        assert GitSourceAdapter().probe_repository(RepositoryLocator(locator), cwd=tmp_path).unwrap()

    @pytest.mark.parametrize(
        "locator", ["https://host/repository", "ssh://host/repository", "file:///missing", "svn+ssh://host/repository"]
    )
    def test_probe_repository__does_not_claim_other_notation(self, tmp_path: Path, locator: str) -> None:
        assert not GitSourceAdapter().probe_repository(RepositoryLocator(locator), cwd=tmp_path).unwrap()

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "https://[invalid",
            "://missing",
            "file://server/shared",
            "file:///source?query=value",
            "filesystem+file:///source",
        ],
    )
    def test_normalize_repository__format_rejection_is_a_non_match(self, tmp_path: Path, value: str) -> None:
        adapter = GitSourceAdapter()
        repository = RepositoryLocator(value)

        assert adapter.normalize_repository(repository, cwd=tmp_path).unwrap() is None
        assert not adapter.probe_repository(repository, cwd=tmp_path).unwrap()

    def test_normalize_repository__retains_operational_errors(self, tmp_path: Path, mocker: MockerFixture) -> None:
        problem = PathResolutionFailed(path="source", reason="Permission denied")
        mocker.patch("vcsinstall.sources.locators.resolve_project_root", return_value=Err([problem]))
        adapter = GitSourceAdapter()
        repository = RepositoryLocator("source")

        assert adapter.normalize_repository(repository, cwd=tmp_path).unwrap_err() == [problem]
        assert adapter.probe_repository(repository, cwd=tmp_path).unwrap_err() == [problem]

    def test_normalize_repository__internal_failures_propagate(self, tmp_path: Path, mocker: MockerFixture) -> None:
        cause = errors.InternalError("Broken path normalization")
        mocker.patch("vcsinstall.sources.locators.resolve_project_root", side_effect=cause)
        adapter = GitSourceAdapter()
        repository = RepositoryLocator("source")

        with pytest.raises(errors.InternalError) as normalized:
            adapter.normalize_repository(repository, cwd=tmp_path)
        with pytest.raises(errors.InternalError) as probed:
            adapter.probe_repository(repository, cwd=tmp_path)
        assert normalized.value is cause
        assert probed.value is cause

    def test_resolve__uses_advertised_head_instead_of_repository_head(self, tmp_path: Path) -> None:
        client = create_repository(tmp_path / "source")
        client.create_branch("stable", checkout=True).unwrap()
        (client.path / "guide.txt").write_bytes(b"stable content")
        revision = SourceRevision(client.commit("Stable branch", all_tracked=True).unwrap())
        client.set_symbolic_reference("HEAD", "refs/heads/trunk").unwrap()
        client.set_symbolic_reference("refs/remotes/origin/HEAD", "refs/heads/stable").unwrap()
        dependency = make.dependency(repository=str(client.path))

        resolved = GitSourceAdapter().resolve(dependency, client.path).unwrap()

        assert resolved.dependency.selector == make.selector("branch", "stable")
        assert resolved.revision == revision
        assert client.symbolic_reference("HEAD").unwrap() == "refs/heads/trunk"

    @pytest.mark.parametrize("head", [None, "refs/tags/release"])
    def test_resolve__requires_advertised_branch_for_omitted_selector(self, tmp_path: Path, head: str | None) -> None:
        client = create_repository(tmp_path / "source")
        client.create_tag("release").unwrap()
        client.set_symbolic_reference("refs/remotes/origin/HEAD", head).unwrap()
        dependency = make.dependency(repository=str(client.path))

        problem = GitSourceAdapter().resolve(dependency, client.path).unwrap_err()[0]

        assert isinstance(problem, errors.RepositoryAccess)
        assert "default branch" in problem.reason

    @pytest.mark.parametrize("selector", ["branch", "tag", "rev"])
    def test_resolve__explicit_selection_needs_no_default_metadata(self, tmp_path: Path, selector: str) -> None:
        client = create_repository(tmp_path / "source")
        client.create_tag("release").unwrap()
        revision = SourceRevision(client.resolve_commit("HEAD").unwrap())
        value = {"branch": "trunk", "tag": "release", "rev": revision}[selector]
        dependency = make.dependency(
            repository=str(client.path), selector=make.selector("revision" if selector == "rev" else selector, value)
        )

        resolved = GitSourceAdapter().resolve(dependency, client.path).unwrap()

        assert resolved.dependency == dependency
        assert resolved.revision == revision

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"selector": {"kind": "branch", "value": "trunk"}},
            {"selector": {"kind": "tag", "value": "Release/One"}},
            {"selector": {"kind": "revision", "value": "ABC123"}},
            {"selector": {"kind": "revision", "value": "a" * 64}},
            {"selector": {"kind": "unresolved", "value": "Release/One"}},
        ],
    )
    def test_validate_input__accepts_supported_selections(self, fields: dict[str, object]) -> None:
        assert GitSourceAdapter().validate_input(make.dependency(**fields)).is_ok()

    @pytest.mark.parametrize(
        "kind,value",
        [("revision", "HEAD~1"), ("revision", "abc"), ("revision", "a" * 65), ("branch", "a..b"), ("tag", "a b")],
    )
    def test_validate_input__rejects_malformed_selections(self, kind: str, value: str) -> None:
        assert (
            GitSourceAdapter()
            .validate_input(make.dependency(selector=make.selector(kind, value)))
            .is_err(InvalidArguments)
        )

    @pytest.mark.parametrize("kind,value", [("revision", "ABC123"), ("unresolved", "release")])
    def test_validate_input__accepts_forms_that_cannot_be_persisted(self, kind: str, value: str) -> None:
        adapter = GitSourceAdapter()
        dependency = make.dependency(selector=make.selector(kind, value))

        assert adapter.validate_input(dependency).is_ok()
        assert adapter.validate_dependency(dependency).is_err()

    def test_copy_file__rejects_invalid_revision(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        create_repository(source)
        resolved = ResolvedSource(
            dependency=make.dependency(repository=str(source), selector=make.selector("branch", "trunk")),
            path=source,
            revision=SourceRevision("invalid"),
        )
        destination = tmp_path / "copied"

        with destination.open("wb") as stream:
            result = GitSourceAdapter().copy_file(resolved, SourcePath(ProjectPathId("@/guide.txt")), stream)

        problem = result.unwrap_err()[0]
        assert isinstance(problem, errors.InvalidRevision)
        assert not destination.read_bytes()

    def test_copy_file__uses_literal_paths_at_the_selected_revision(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        for name in ("first", "second"):
            (source / name).mkdir()
            (source / name / "file").write_bytes(name.encode())
        client.add(".").unwrap()
        revision = SourceRevision(client.commit("Selected paths").unwrap())
        resolved = ResolvedSource(
            dependency=make.dependency(repository=str(source), selector=make.selector("branch", "trunk")),
            path=source,
            revision=revision,
        )
        paths = tuple(SourcePath(ProjectPathId(path)) for path in ("@/first", "@/second", "@/first/file"))
        adapter = GitSourceAdapter()
        files = ("@/first/file", "@/second/file", "@/first/file")
        (source / "first" / "file").write_bytes(b"new revision")
        client.commit("Advance source", all_tracked=True).unwrap()
        content = adapter.inspect(resolved, paths).unwrap()
        assert content[paths[0]].file_hashes == {"@/file": hashes.hash_file(b"first")}

        for index, (file, expected) in enumerate(zip(files, (b"first", b"second", b"first"), strict=True)):
            destination = tmp_path / f"copied-{index}"
            with destination.open("wb") as stream:
                adapter.copy_file(resolved, SourcePath(ProjectPathId(file)), stream).unwrap()
            assert destination.read_bytes() == expected

    def test_inspect__hashes_each_selected_blob_once_across_paths(self, tmp_path: Path, mocker: MockerFixture) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        (source / "duplicate").write_bytes(b"original\n")
        client.add(".").unwrap()
        revision = SourceRevision(client.commit("Duplicate content").unwrap())
        resolved = ResolvedSource(
            dependency=make.dependency(repository=str(source), selector=make.selector("branch", "trunk")),
            path=source,
            revision=revision,
        )
        paths = (SourcePath(ProjectPathId("@/")), SourcePath(ProjectPathId("@/guide.txt")))
        inspected = mocker.spy(GitClient, "hash_blob")

        trees = GitSourceAdapter().inspect(resolved, paths).unwrap()

        assert inspected.call_count == 1
        assert trees[paths[0]].file_hashes == {
            "@/duplicate": hashes.hash_file(b"original\n"),
            "@/guide.txt": hashes.hash_file(b"original\n"),
        }
        assert trees[paths[1]].file_hashes == {"@/": hashes.hash_file(b"original\n")}

    @pytest.mark.skipif(os.name != "posix", reason="Raw POSIX filenames and symbolic links")
    @pytest.mark.parametrize("unsupported", ["symlink", "gitlink", "non_utf8"])
    def test_inspect__validates_only_selected_content(self, tmp_path: Path, unsupported: str) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        parent = client.resolve_commit("HEAD").unwrap()
        if unsupported == "symlink":
            (source / "unsupported").symlink_to("guide.txt")
        elif unsupported == "non_utf8":
            (source / os.fsdecode(b"\xff")).write_bytes(b"non-UTF8 name")
        client.add(".").unwrap()
        if unsupported == "gitlink":
            client.run(["update-index", "--add", "--cacheinfo", f"160000,{parent},unsupported"]).unwrap()
        client.commit("Unsupported entry").unwrap()
        revision = SourceRevision(client.resolve_commit("HEAD").unwrap())
        resolved = ResolvedSource(
            dependency=make.dependency(repository=str(source), selector=make.selector("branch", "trunk")),
            path=source,
            revision=revision,
        )
        adapter = GitSourceAdapter()

        path = SourcePath(ProjectPathId("@/guide.txt"))
        selected = adapter.inspect(resolved, (path,)).unwrap()[path]

        assert selected.kind == ContentKind.file
        assert selected.files[0].path == "@/"
        assert selected.files[0].hash == hashes.hash_file(b"original\n")
        problem = adapter.inspect(resolved, (SourcePath(ProjectPathId("@/")),)).unwrap_err()[0]
        assert isinstance(problem, errors.RepositoryAccess)
        assert problem.repository == resolved.dependency.repository

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"selector": {"kind": "branch", "value": "main"}},
            {"selector": {"kind": "tag", "value": "Release+%2F"}},
            {"selector": {"kind": "tag", "value": "HEAD"}},
            {"selector": {"kind": "revision", "value": "1" * 40}},
            {"selector": {"kind": "revision", "value": "a" * 64}},
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
        result = GitSourceAdapter().validate_dependency(
            make.dependency(selector=make.selector("revision" if field == "rev" else field, value))
        )

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.InvalidGitReference)
        assert error.field == field
        assert error.value == value

    @pytest.mark.parametrize("value", ["HEAD", "-branch"])
    def test_validate_dependency__rejects_special_branches(self, value: str) -> None:
        result = GitSourceAdapter().validate_dependency(make.dependency(selector=make.selector("branch", value)))

        assert isinstance(result.unwrap_err()[0], errors.InvalidGitReference)

    @pytest.mark.parametrize("revision", ["HEAD", "main~2", "a" * 39, "A" * 40, "sha256:" + "a" * 64])
    def test_validate_dependency__rejects_noncomplete_revision(self, revision: str) -> None:
        error = (
            GitSourceAdapter()
            .validate_dependency(make.dependency(selector=make.selector("revision", revision)))
            .unwrap_err()[0]
        )

        assert isinstance(error, errors.InvalidRevision)
        assert error.field == "rev"
        assert error.value == revision

    @pytest.mark.parametrize("revision", ["1" * 40, "a" * 64])
    @pytest.mark.parametrize("field", ["branch", "tag", "rev"])
    def test_validate_revision_lock__accepts_complete_revision(self, revision: str, field: str) -> None:
        dependency = make.dependency(
            selector=make.selector(
                "revision" if field == "rev" else field, revision if field == "rev" else "Release/One"
            )
        )

        assert GitSourceAdapter().validate_revision_lock(dependency, SourceRevision(revision)).is_ok()

    def test_validate_revision_lock__requires_selection(self) -> None:
        result = GitSourceAdapter().validate_revision_lock(make.dependency(), SourceRevision("1" * 40))

        assert isinstance(result.unwrap_err()[0], errors.MissingGitSelector)

    @pytest.mark.parametrize("revision", ["", "HEAD", "a" * 39, "A" * 40])
    def test_validate_revision_lock__rejects_invalid_revision(self, revision: str) -> None:
        result = GitSourceAdapter().validate_revision_lock(
            make.dependency(selector=make.selector("branch", "main")), SourceRevision(revision)
        )

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.InvalidRevision)
        assert error.field == "revision"
        assert error.value == revision

    def test_validate_revision_lock__checks_declaration(self) -> None:
        result = GitSourceAdapter().validate_revision_lock(
            make.dependency(selector=make.selector("branch", "bad..branch")), SourceRevision("1" * 40)
        )

        assert isinstance(result.unwrap_err()[0], errors.InvalidGitReference)

    def test_validate_revision_lock__rejects_different_pin(self) -> None:
        result = GitSourceAdapter().validate_revision_lock(
            make.dependency(selector=make.selector("revision", "1" * 40)), SourceRevision("2" * 40)
        )

        error = result.unwrap_err()[0]
        assert isinstance(error, errors.RevisionMismatch)
        assert error.expected == "1" * 40
        assert error.revision == "2" * 40
