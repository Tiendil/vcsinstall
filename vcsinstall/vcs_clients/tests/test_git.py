import os
import subprocess  # noqa: S404 — Simulate the native command boundary for failure tests.
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from vcsinstall.domain import RepositoryLocator, VcsId
from vcsinstall.filesystem import hashes
from vcsinstall.vcs_clients import GitClient, GitObjectId, GitObjectKind, clone_mirror, errors
from vcsinstall.vcs_clients.tests.helpers import create_repository


class TestGitClient:
    @pytest.fixture
    def client(self, tmp_path: Path) -> GitClient:
        return create_repository(tmp_path / "source")

    @pytest.mark.parametrize("layout", ["worktree", "bare", "linked"])
    def test_is_repository__recognizes_exact_native_roots(
        self, client: GitClient, tmp_path: Path, layout: str
    ) -> None:
        path = client.path
        if layout == "bare":
            path = tmp_path / "bare"
            clone_mirror(client.repository, path).unwrap()
        elif layout == "linked":
            path = tmp_path / "linked"
            client.run(["worktree", "add", "--detach", "--", str(path), "HEAD"]).unwrap()
            assert (path / ".git").is_file()
        original = tuple((client.path / ".git" / name).read_bytes() for name in ("HEAD", "index", "config"))
        candidate = GitClient(path=path, repository=RepositoryLocator(str(path)))

        assert candidate.is_repository().unwrap()

        assert tuple((client.path / ".git" / name).read_bytes() for name in ("HEAD", "index", "config")) == original

    @pytest.mark.parametrize("exists", [False, True])
    def test_is_repository__does_not_discover_an_ancestor(self, client: GitClient, exists: bool) -> None:
        path = client.path / "nested"
        if exists:
            path.mkdir()

        assert not GitClient(path=path, repository=client.repository).is_repository().unwrap()

    def test_is_repository__retains_invalid_repository_errors(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        client = GitClient(path=tmp_path, repository=RepositoryLocator(str(tmp_path)))

        problem = client.is_repository().unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == client.repository
        assert problem.returncode

    def test_is_repository__retains_access_failures(self, client: GitClient, mocker: MockerFixture) -> None:
        cause = PermissionError("Cannot inspect repository metadata")
        mocker.patch("vcsinstall.vcs_clients.git.Path.exists", side_effect=cause)

        problem = client.is_repository().unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.cause is cause

    @pytest.mark.parametrize("empty", [False, True])
    def test_probe_remote__accepts_native_repositories_without_cloning(self, tmp_path: Path, empty: bool) -> None:
        path = tmp_path / "source"
        if empty:
            client = GitClient(path=path, repository=RepositoryLocator(str(path)))
            client.initialize(initial_branch="trunk").unwrap()
        else:
            client = create_repository(path)
        before = sorted(file.relative_to(path) for file in path.rglob("*") if file.is_file())

        assert GitClient.probe_remote(RepositoryLocator(path.as_uri())).unwrap()

        assert sorted(file.relative_to(path) for file in path.rglob("*") if file.is_file()) == before

    def test_probe_remote__retains_access_failures(self, tmp_path: Path) -> None:
        repository = RepositoryLocator(str(tmp_path / "missing"))

        problem = GitClient.probe_remote(repository).unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == repository
        assert problem.returncode

    def test_probe_remote__bounds_execution_and_retains_timeout(self, mocker: MockerFixture) -> None:
        repository = RepositoryLocator("https://example.test/shared")
        cause = subprocess.TimeoutExpired(cmd=["git", "ls-remote"], timeout=0.1)
        invocation = mocker.patch("vcsinstall.vcs_clients.git.subprocess.run", side_effect=cause)

        problem = GitClient.probe_remote(repository, timeout=0.1).unwrap_err()[0]

        assert invocation.call_args.kwargs["timeout"] == 0.1
        assert invocation.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == repository
        assert problem.cause is cause
        assert "timed out" in problem.reason
        assert problem.ways_to_fix

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    def test_initialize__creates_repository_with_requested_branch_and_format(
        self, tmp_path: Path, object_format: str
    ) -> None:
        path = tmp_path / "new repository"
        client = GitClient(path=path, repository=RepositoryLocator(str(path)))

        client.initialize(initial_branch="feature/start", object_format=object_format).unwrap()

        assert (path / ".git").is_dir()
        assert client.run(["symbolic-ref", "HEAD"]).unwrap() == b"refs/heads/feature/start\n"
        assert client.run(["rev-parse", "--show-object-format"]).unwrap() == object_format.encode() + b"\n"

    def test_initialize__reports_obstructed_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "file"
        path.write_bytes(b"keep")
        client = GitClient(path=path, repository=RepositoryLocator(str(path)))

        problem = client.initialize(initial_branch="trunk").unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == client.repository
        assert problem.reason
        assert path.read_bytes() == b"keep"

    def test_set_config__writes_repository_local_value(self, client: GitClient) -> None:
        client.set_config("user.name", "Local Test Identity").unwrap()

        assert client.run(["config", "--local", "--get", "user.name"]).unwrap() == b"Local Test Identity\n"

    def test_commit__records_only_staged_content(self, client: GitClient) -> None:
        (client.path / "--payload").write_bytes(b"committed\x00bytes")
        (client.path / "guide.txt").write_bytes(b"unstaged")
        client.add("--payload").unwrap()
        message = "Record staged content\n\nKeep the working tree separate."

        revision = client.commit(message).unwrap()

        entries = {entry.path: entry for entry in client.list_tree(revision).unwrap()}
        assert client.hash_blob(entries[b"--payload"].object_id).unwrap() == hashes.hash_file(b"committed\x00bytes")
        assert client.hash_blob(entries[b"guide.txt"].object_id).unwrap() == hashes.hash_file(b"original\n")
        assert (client.path / "guide.txt").read_bytes() == b"unstaged"
        assert client.run(["show", "-s", "--format=%B", revision]).unwrap().decode().strip() == message

    def test_commit__all_tracked_leaves_untracked_files_out(self, client: GitClient) -> None:
        (client.path / "guide.txt").write_bytes(b"updated")
        (client.path / "untracked").write_bytes(b"leave out")

        revision = client.commit("Update tracked files", all_tracked=True).unwrap()

        entries = client.list_tree(revision).unwrap()
        assert tuple(entry.path for entry in entries) == (b"guide.txt",)
        assert client.hash_blob(entries[0].object_id).unwrap() == hashes.hash_file(b"updated")

    def test_commit__failure_preserves_head(self, client: GitClient) -> None:
        previous = client.resolve_commit("HEAD").unwrap()

        problem = client.commit("No staged changes").unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == client.repository
        assert client.resolve_commit("HEAD").unwrap() == previous

    @pytest.mark.parametrize("checkout", [False, True])
    def test_create_branch__optionally_checks_out_new_branch(self, client: GitClient, checkout: bool) -> None:
        previous = client.resolve_commit("HEAD").unwrap()

        client.create_branch("feature/new", checkout=checkout).unwrap()

        expected = b"refs/heads/feature/new\n" if checkout else b"refs/heads/trunk\n"
        assert client.run(["symbolic-ref", "HEAD"]).unwrap() == expected
        assert client.resolve_commit("refs/heads/feature/new").unwrap() == previous
        assert client.resolve_commit("refs/heads/trunk").unwrap() == previous

    def test_delete_tag__preserves_same_named_branch(self, client: GitClient) -> None:
        client.create_tag("trunk").unwrap()

        client.delete_tag("trunk").unwrap()

        assert not client.reference_exists("refs/tags/trunk").unwrap()
        assert client.reference_exists("refs/heads/trunk").unwrap()

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    def test_resolve_commit__full_and_abbreviated_identifiers(self, tmp_path: Path, object_format: str) -> None:
        source = tmp_path / "source"
        client = create_repository(source, object_format=object_format)
        revision = client.resolve_object("HEAD").unwrap()

        assert client.resolve_commit(revision).unwrap() == revision
        assert client.resolve_commit(revision[:12]).unwrap() == revision
        assert client.resolve_commit("refs/heads/trunk", peel=True).unwrap() == revision

    @pytest.mark.parametrize("object_name", ["HEAD^{tree}", "HEAD:guide.txt", "refs/tags/release"])
    def test_resolve_commit__rejects_noncommit_objects(self, client: GitClient, object_name: str) -> None:
        client.create_tag("release", message="Release").unwrap()
        object_id = client.resolve_object(object_name).unwrap()

        problem = client.resolve_commit(object_id).unwrap_err()[0]

        assert isinstance(problem, errors.InvalidCommit)
        assert problem.repository == client.repository
        assert problem.value == object_id
        assert problem.ways_to_fix

    def test_resolve_commit__peels_annotated_tags(self, client: GitClient) -> None:
        client.create_tag("release", message="Release").unwrap()

        assert client.resolve_commit("refs/tags/release", peel=True).unwrap() == client.resolve_object("HEAD").unwrap()

    def test_reference_exists__uses_exact_namespaces(self, client: GitClient) -> None:
        assert client.reference_exists("refs/heads/trunk").unwrap()
        assert not client.reference_exists("refs/tags/trunk").unwrap()
        client.create_tag("trunk").unwrap()
        assert client.reference_exists("refs/tags/trunk").unwrap()
        assert not client.reference_exists("refs/heads/trunk/absent").unwrap()

    def test_reference_exists__preserves_command_failures(self, tmp_path: Path) -> None:
        client = GitClient(path=tmp_path / "missing", repository=RepositoryLocator("diagnostic-locator"))

        problem = client.reference_exists("refs/heads/trunk").unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == client.repository
        assert problem.returncode is not None and problem.returncode != 1
        assert problem.reason

    def test_symbolic_reference__distinguishes_symbolic_direct_and_missing_refs(self, client: GitClient) -> None:
        assert client.symbolic_reference("HEAD").unwrap() == "refs/heads/trunk"
        assert client.symbolic_reference("refs/heads/trunk").unwrap() is None
        assert client.symbolic_reference("refs/remotes/origin/HEAD").unwrap() is None

    def test_symbolic_reference__preserves_command_failures(self, tmp_path: Path) -> None:
        client = GitClient(path=tmp_path / "missing", repository=RepositoryLocator("diagnostic-locator"))

        problem = client.symbolic_reference("HEAD").unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == client.repository
        assert problem.returncode is not None and problem.returncode != 1

    def test_set_symbolic_reference__clears_only_the_symbolic_ref(self, client: GitClient) -> None:
        revision = client.resolve_commit("HEAD").unwrap()
        client.set_symbolic_reference("refs/remotes/origin/HEAD", "refs/heads/trunk").unwrap()
        assert client.symbolic_reference("refs/remotes/origin/HEAD").unwrap() == "refs/heads/trunk"

        client.set_symbolic_reference("refs/remotes/origin/HEAD", None).unwrap()
        client.set_symbolic_reference("refs/remotes/origin/HEAD", None).unwrap()

        assert client.symbolic_reference("refs/remotes/origin/HEAD").unwrap() is None
        assert client.resolve_commit("refs/heads/trunk").unwrap() == revision
        assert client.symbolic_reference("HEAD").unwrap() == "refs/heads/trunk"

    def test_refresh_remote_head__tracks_the_source_independently_of_cached_head(
        self, client: GitClient, tmp_path: Path
    ) -> None:
        client.create_branch("stable").unwrap()
        mirror = tmp_path / "mirror.git"
        clone_mirror(client.repository, mirror).unwrap()
        cached = GitClient(path=mirror, repository=client.repository)
        cached.refresh_remote_head().unwrap()
        assert cached.symbolic_reference("refs/remotes/origin/HEAD").unwrap() == "refs/heads/trunk"
        client.set_symbolic_reference("HEAD", "refs/heads/stable").unwrap()

        cached.refresh_remote_head().unwrap()

        assert cached.symbolic_reference("refs/remotes/origin/HEAD").unwrap() == "refs/heads/stable"
        assert cached.symbolic_reference("HEAD").unwrap() == "refs/heads/trunk"
        assert client.symbolic_reference("HEAD").unwrap() == "refs/heads/stable"

    def test_refresh_remote_head__clears_stale_metadata_when_source_head_is_detached(
        self, client: GitClient, tmp_path: Path
    ) -> None:
        mirror = tmp_path / "mirror.git"
        clone_mirror(client.repository, mirror).unwrap()
        cached = GitClient(path=mirror, repository=client.repository)
        cached.refresh_remote_head().unwrap()
        revision = client.resolve_commit("HEAD").unwrap()
        client.run(["checkout", "--detach", revision, "--"]).unwrap()

        cached.refresh_remote_head().unwrap()

        assert cached.symbolic_reference("refs/remotes/origin/HEAD").unwrap() is None
        assert cached.symbolic_reference("HEAD").unwrap() == "refs/heads/trunk"
        assert cached.resolve_commit("refs/heads/trunk").unwrap() == revision

    def test_refresh_remote_head__retains_transport_failures(self, client: GitClient, tmp_path: Path) -> None:
        cached = GitClient(path=client.path, repository=RepositoryLocator(str(tmp_path / "missing")))

        problem = cached.refresh_remote_head().unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == cached.repository
        assert problem.returncode

    @pytest.mark.parametrize("payload", [b"", b"stored\x00bytes\r\n$Format:%H$\n"])
    def test_hash_blob__hashes_exact_committed_bytes(self, client: GitClient, payload: bytes) -> None:
        (client.path / "guide.txt").write_bytes(payload)
        revision = client.commit("Replace content", all_tracked=True).unwrap()
        entry = client.list_tree(revision).unwrap()[0]
        (client.path / "guide.txt").write_bytes(b"uncommitted")

        assert client.hash_blob(entry.object_id).unwrap() == hashes.hash_file(payload)

    def test_hash_blob__rejects_missing_object(self, client: GitClient) -> None:
        problem = client.hash_blob(GitObjectId("0" * 40)).unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == client.repository
        assert problem.returncode

    def test_hash_blob__retains_launch_failure(self, client: GitClient, mocker: MockerFixture) -> None:
        cause = FileNotFoundError("Git executable unavailable")
        mocker.patch("vcsinstall.vcs_clients.git.subprocess.Popen", side_effect=cause)

        problem = client.hash_blob(GitObjectId("0" * 40)).unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.cause is cause

    @pytest.mark.parametrize("payload", [b"", b"stored\x00bytes\r\n$Format:%H$\n"])
    def test_copy_blob__copies_committed_bytes_without_working_tree_changes(
        self, client: GitClient, tmp_path: Path, payload: bytes
    ) -> None:
        (client.path / "guide.txt").write_bytes(payload)
        (client.path / ".gitattributes").write_text("guide.txt export-ignore export-subst\n")
        client.add(".").unwrap()
        revision = client.commit("Replace content").unwrap()
        entry = next(entry for entry in client.list_tree(revision).unwrap() if entry.path == b"guide.txt")
        (client.path / "guide.txt").write_bytes(b"uncommitted")
        index = (client.path / ".git" / "index").read_bytes()
        destination = tmp_path / "copied"

        with destination.open("wb") as stream:
            client.copy_blob(entry.object_id, stream).unwrap()

        assert destination.read_bytes() == payload
        assert (client.path / "guide.txt").read_bytes() == b"uncommitted"
        assert (client.path / ".git" / "index").read_bytes() == index

    def test_copy_blob__rejects_missing_object(self, client: GitClient, tmp_path: Path) -> None:
        with (tmp_path / "copied").open("wb") as destination:
            problem = client.copy_blob(GitObjectId("0" * 40), destination).unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == client.repository
        assert problem.returncode

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("name", ["plain", "--option", "colon:name", "[literal]*?.txt", "space ü\tname"])
    def test_copy_file__uses_literal_paths_and_exact_committed_bytes(
        self, tmp_path: Path, object_format: str, name: str
    ) -> None:
        client = create_repository(tmp_path / "source", object_format=object_format)
        payload = b"stored\x00bytes\r\n$Format:%H$\n"
        path = client.path / name
        path.write_bytes(payload)
        (client.path / ".gitattributes").write_text("* export-ignore export-subst\n")
        client.add(".").unwrap()
        revision = client.commit("Literal file").unwrap()
        path.write_bytes(b"uncommitted")
        index = (client.path / ".git" / "index").read_bytes()
        destination = tmp_path / "copied"

        with destination.open("wb") as stream:
            client.copy_file(revision, name, stream).unwrap()

        assert destination.read_bytes() == payload
        assert path.read_bytes() == b"uncommitted"
        assert (client.path / ".git" / "index").read_bytes() == index

    def test_copy_file__handles_empty_file(self, client: GitClient, tmp_path: Path) -> None:
        (client.path / "guide.txt").write_bytes(b"")
        revision = client.commit("Empty file", all_tracked=True).unwrap()
        destination = tmp_path / "copied"

        with destination.open("wb") as stream:
            client.copy_file(revision, "guide.txt", stream).unwrap()

        assert not destination.read_bytes()

    def test_copy_file__preserves_missing_path_diagnostic(self, client: GitClient, tmp_path: Path) -> None:
        revision = client.resolve_commit("HEAD").unwrap()
        with (tmp_path / "copied").open("wb") as stream:
            problem = client.copy_file(revision, "missing", stream).unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.repository == client.repository
        assert problem.returncode

    def test_copy_file__retains_output_failure(self, client: GitClient, tmp_path: Path) -> None:
        revision = client.resolve_commit("HEAD").unwrap()
        destination = tmp_path / "readonly"
        destination.write_bytes(b"keep")
        with destination.open("rb") as stream:
            problem = client.copy_file(revision, "guide.txt", stream).unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.returncode
        assert problem.reason
        assert destination.read_bytes() == b"keep"

    @pytest.mark.skipif(os.name != "posix", reason="Raw POSIX filenames and symbolic links")
    def test_list_tree__preserves_raw_names_and_all_git_modes(self, client: GitClient) -> None:
        parent = client.resolve_object("HEAD").unwrap()
        raw_name = b" raw-\xff\tname\n "
        (client.path / os.fsdecode(raw_name)).write_bytes(b"literal")
        directory = client.path / "directory"
        directory.mkdir()
        executable = directory / "script"
        executable.write_bytes(b"executable")
        executable.chmod(0o755)
        (client.path / "link").symlink_to("guide.txt")
        client.add(".").unwrap()
        client.run(["update-index", "--add", "--cacheinfo", f"160000,{parent},submodule"]).unwrap()
        client.commit("Store metadata").unwrap()

        entries = {entry.path: entry for entry in client.list_tree("HEAD").unwrap()}

        assert raw_name in entries
        assert client.hash_blob(entries[raw_name].object_id).unwrap() == hashes.hash_file(b"literal")
        assert entries[b"directory"].kind == GitObjectKind.tree
        assert entries[b"directory"].mode == b"040000"
        assert entries[b"directory/script"].mode == b"100755"
        assert entries[b"link"].kind == GitObjectKind.blob
        assert entries[b"link"].mode == b"120000"
        assert entries[b"submodule"].kind == GitObjectKind.commit
        assert entries[b"submodule"].mode == b"160000"
        assert entries[b"submodule"].object_id == parent

    def test_list_tree__empty_committed_tree(self, client: GitClient) -> None:
        client.remove("guide.txt").unwrap()
        client.commit("Empty tree").unwrap()

        assert client.list_tree("HEAD").unwrap() == ()

    def test_run__retains_native_diagnostics(self, client: GitClient) -> None:
        problem = client.run(["not-a-command"]).unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.vcs == VcsId.git
        assert problem.repository == client.repository
        assert problem.returncode
        assert problem.reason
        assert problem.ways_to_fix

    def test_run__retains_launch_failure_cause(self, client: GitClient, mocker: MockerFixture) -> None:
        cause = FileNotFoundError("Git executable unavailable")
        mocker.patch("vcsinstall.vcs_clients.git.subprocess.run", side_effect=cause)

        problem = client.run(["status"]).unwrap_err()[0]

        assert isinstance(problem, errors.CommandFailed)
        assert problem.returncode is None
        assert problem.reason == str(cause)
        assert problem.cause is cause


class TestCloneMirror:
    def test_committed_clone_preserves_original_repository(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source_client = create_repository(source)
        revision = source_client.resolve_commit("HEAD").unwrap()
        (source / "guide.txt").write_bytes(b"uncommitted")
        index = (source / ".git" / "index").read_bytes()
        config = (source / ".git" / "config").read_bytes()
        destination = tmp_path / "mirror.git"
        locator = RepositoryLocator(str(source))

        clone_mirror(locator, destination).unwrap()
        client = GitClient(path=destination, repository=locator)
        entry = client.list_tree(revision).unwrap()[0]

        assert client.run(["rev-parse", "--is-bare-repository"]).unwrap() == b"true\n"
        assert client.hash_blob(entry.object_id).unwrap() == hashes.hash_file(b"original\n")
        assert (source / "guide.txt").read_bytes() == b"uncommitted"
        assert (source / ".git" / "index").read_bytes() == index
        assert (source / ".git" / "config").read_bytes() == config
