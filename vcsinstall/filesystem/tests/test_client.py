import io
import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pytest
from llm_tool_cli.core.result import Err, Result
from llm_tool_cli.paths import ProjectPathId, resolve_project_root
from llm_tool_cli.paths.errors import InvalidProjectPath
from pytest_mock import MockerFixture

from vcsinstall.domain import ContentKind
from vcsinstall.filesystem import FSClient, errors, hashes

type FileWriter = Callable[[BinaryIO], Result[None]]


class TestFSClient:
    @pytest.fixture
    def client(self, tmp_path: Path) -> FSClient:
        root = tmp_path / "destination"
        root.mkdir()
        return FSClient(root=resolve_project_root(root).unwrap())

    @pytest.fixture
    def source(self, tmp_path: Path) -> FSClient:
        root = tmp_path / "source"
        root.mkdir()
        (root / "payload").write_bytes(b"new")
        return FSClient(root=resolve_project_root(root).unwrap())

    @pytest.fixture
    def writer(self, client: FSClient) -> FSClient:
        return FSClient(root=client.root, readonly=False)

    def test_resolve_path__allows_missing_targets_without_creation(self, client: FSClient) -> None:
        target: Path = client.root / "missing/target"

        assert client.resolve_path(ProjectPathId("@/missing/target")).unwrap() == target
        assert not target.parent.exists()

    def test_resolve_path__rejects_a_symlink_parent(self, client: FSClient, tmp_path: Path) -> None:
        (client.root / "link").symlink_to(tmp_path, target_is_directory=True)

        result = client.resolve_path(ProjectPathId("@/link/target"))

        assert result.is_err(InvalidProjectPath)
        assert (client.root / "link").is_symlink()

    @pytest.fixture
    def write_source(self, source: FSClient) -> FileWriter:
        source.inspect_tree(ProjectPathId("@/payload")).unwrap()

        def write(destination: BinaryIO) -> Result[None]:
            return source.copy_file(ProjectPathId("@/payload"), destination)

        return write

    @pytest.mark.parametrize("existing", [False, True])
    def test_write_file__readonly_refuses_creation_and_overwrite(self, client: FSClient, existing: bool) -> None:
        target = client.root / "target"
        if existing:
            target.write_bytes(b"keep")

        assert client.write_file(ProjectPathId("@/target"), b"new").is_err(errors.ReadOnly)
        if existing:
            assert target.read_bytes() == b"keep"
        else:
            assert not target.exists()

    def test_write_file__rejects_escape_from_root(self, writer: FSClient) -> None:
        nested = writer.root / "nested"
        nested.mkdir()
        scoped = FSClient(root=resolve_project_root(nested).unwrap(), readonly=False)
        outside = writer.root / "outside"
        outside.write_bytes(b"keep")

        assert scoped.write_file(ProjectPathId("@/../outside"), b"new").is_err(InvalidProjectPath)
        assert outside.read_bytes() == b"keep"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symbolic links")
    def test_write_file__rejects_symlink_destination(self, writer: FSClient) -> None:
        target = writer.root / "target"
        target.write_bytes(b"keep")
        (writer.root / "link").symlink_to("target")

        assert writer.write_file(ProjectPathId("@/link"), b"new").is_err(InvalidProjectPath)
        assert target.read_bytes() == b"keep"

    @pytest.mark.parametrize("kind", [ContentKind.file, ContentKind.directory, ContentKind.absent])
    def test_inspect_kind__distinguishes_files_directories_and_absence(
        self, client: FSClient, kind: ContentKind
    ) -> None:
        path = client.root / "selected"
        if kind == ContentKind.file:
            path.write_bytes(b"content")
        elif kind == ContentKind.directory:
            path.mkdir()

        assert client.inspect_kind(ProjectPathId("@/selected")).unwrap() == kind
        assert client.inspect_tree(ProjectPathId("@/selected")).unwrap().kind == kind

    def test_inspect_tree__hashes_selected_content_with_relative_paths(self, client: FSClient) -> None:
        directory = client.root / "selected"
        directory.mkdir()
        (directory / "nested").mkdir()
        (directory / "empty").mkdir()
        (directory / "nested" / "payload").write_bytes(b"literal\x00bytes\r\n")
        (client.root / "unselected").write_bytes(b"other")

        tree = client.inspect_tree(ProjectPathId("@/selected")).unwrap()

        assert tree.kind == ContentKind.directory
        assert tuple(file.path for file in tree.files) == ("@/nested/payload",)
        assert tree.files[0].hash == hashes.hash_file(b"literal\x00bytes\r\n")
        assert client.inspect_tree(ProjectPathId("@/selected/empty")).unwrap().files == ()
        file = client.inspect_tree(ProjectPathId("@/selected/nested/payload")).unwrap().files[0]
        assert file.path == "@/"
        assert file.hash == tree.files[0].hash

    @pytest.mark.skipif(os.name != "posix", reason="POSIX executable permissions")
    def test_inspect_tree__reports_exclusions_without_traversal(self, client: FSClient) -> None:
        (client.root / ".Git").mkdir()
        (client.root / ".Git/link").symlink_to("missing")
        (client.root / "nested").mkdir()
        (client.root / "nested/.git").write_bytes(b"marker")
        (client.root / "nested/keep").write_bytes(b"keep")

        tree = client.inspect_tree(exclude_names=frozenset({".git"})).unwrap()

        assert [file.path for file in tree.files] == ["@/nested/keep"]
        assert tree.excluded_paths == ("@/.Git", "@/nested/.git")
        assert (client.root / ".Git/link").is_symlink()

    def test_inspect_tree__retains_executable_metadata(self, client: FSClient) -> None:
        path = client.root / "script"
        path.write_bytes(b"executable")
        path.chmod(0o755)

        assert client.inspect_tree(ProjectPathId("@/script")).unwrap().files[0].executable

    @pytest.mark.skipif(os.name != "posix", reason="Raw POSIX filenames")
    def test_inspect_tree__rejects_unrepresentable_names(self, client: FSClient) -> None:
        path = client.root / os.fsdecode(b"invalid-\xff")
        path.write_bytes(b"keep")

        problem = client.inspect_tree().unwrap_err()[0]

        assert isinstance(problem, errors.AccessFailed)
        assert problem.cause is not None
        assert path.read_bytes() == b"keep"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symbolic links")
    @pytest.mark.parametrize("dangling", [False, True])
    def test_inspect_tree__rejects_links_below_root(self, client: FSClient, dangling: bool) -> None:
        if not dangling:
            (client.root / "target").write_bytes(b"keep")
        (client.root / "link").symlink_to("target")

        result = client.inspect_tree()

        assert result.is_err(InvalidProjectPath)
        assert (client.root / "link").is_symlink()

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Named pipes")
    def test_inspect_tree__rejects_unsupported_objects(self, client: FSClient) -> None:
        os.mkfifo(client.root / "pipe")

        assert client.inspect_tree().is_err(errors.AccessFailed)
        assert client.inspect_kind(ProjectPathId("@/pipe")).is_err(errors.AccessFailed)

    def test_inspect_tree__retains_read_failure(self, client: FSClient, mocker: MockerFixture) -> None:
        (client.root / "file").write_bytes(b"keep")
        cause = OSError("cannot read")
        mocker.patch("pathlib.Path.open", side_effect=cause)

        problem = client.inspect_tree(ProjectPathId("@/file")).unwrap_err()[0]

        assert isinstance(problem, errors.AccessFailed)
        assert problem.path == client.root / "file"
        assert problem.cause is cause

    @pytest.mark.parametrize("payload", [b"", b"literal\x00bytes\r\n"])
    def test_copy_file__streams_exact_bytes(self, source: FSClient, payload: bytes) -> None:
        (source.root / "payload").write_bytes(payload)
        source.inspect_tree(ProjectPathId("@/payload")).unwrap()
        destination = io.BytesIO()

        source.copy_file(ProjectPathId("@/payload"), destination).unwrap()

        assert destination.getvalue() == payload

    def test_copy_file__rejects_outside_source(self, source: FSClient) -> None:
        destination = io.BytesIO()

        assert source.copy_file(ProjectPathId("@/../outside"), destination).is_err(InvalidProjectPath)
        assert not destination.getvalue()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symbolic links")
    def test_copy_file__rejects_symlink_source(self, source: FSClient) -> None:
        (source.root / "link").symlink_to("payload")
        destination = io.BytesIO()

        assert source.copy_file(ProjectPathId("@/link"), destination).is_err(InvalidProjectPath)
        assert not destination.getvalue()

    def test_copy_file__retains_read_failure(self, source: FSClient, mocker: MockerFixture) -> None:
        source.inspect_tree(ProjectPathId("@/payload")).unwrap()
        cause = OSError("cannot read source")
        mocker.patch("pathlib.Path.open", side_effect=cause)

        problem = source.copy_file(ProjectPathId("@/payload"), io.BytesIO()).unwrap_err()[0]

        assert isinstance(problem, errors.AccessFailed)
        assert problem.cause is cause
        assert problem.path == source.root / "payload"

    @pytest.mark.parametrize("existing", ["missing", "file", "empty_tree"])
    def test_write_stream__readonly_prevents_effects(
        self, client: FSClient, write_source: FileWriter, existing: str
    ) -> None:
        target = client.root / "target"
        if existing == "file":
            target.write_bytes(b"keep")
        elif existing == "empty_tree":
            (target / "nested").mkdir(parents=True)
        before = client.inspect_tree().unwrap()
        before_paths = tuple(client.root.rglob("*"))

        result = client.write_stream(ProjectPathId("@/target"), write_source)

        assert result.is_err(errors.ReadOnly)
        assert client.inspect_tree().unwrap() == before
        assert tuple(client.root.rglob("*")) == before_paths

    @pytest.mark.parametrize("existing", ["missing", "file"])
    def test_write_stream__creates_one_file_and_required_parents(
        self, writer: FSClient, write_source: FileWriter, existing: str
    ) -> None:
        target = writer.root / "new" / "target"
        if existing == "file":
            target.parent.mkdir()
            target.write_bytes(b"old")

        writer.write_stream(ProjectPathId("@/new/target"), write_source).unwrap()

        assert target.is_file()
        assert target.read_bytes() == b"new"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX executable permissions")
    def test_write_stream__preserves_selected_executable_metadata(
        self, writer: FSClient, write_source: FileWriter
    ) -> None:
        writer.write_stream(ProjectPathId("@/script"), write_source, executable=True).unwrap()

        assert (writer.root / "script").stat().st_mode & 0o111

    @pytest.mark.parametrize("nonempty", [False, True])
    def test_write_stream__cannot_replace_directory(
        self, writer: FSClient, write_source: FileWriter, nonempty: bool
    ) -> None:
        target = writer.root / "target"
        target.mkdir()
        if nonempty:
            (target / "keep").write_bytes(b"keep")

        result = writer.write_stream(ProjectPathId("@/target"), write_source)

        assert result.is_err(errors.AccessFailed)
        assert target.is_dir()
        if nonempty:
            assert (target / "keep").read_bytes() == b"keep"

    def test_write_stream__preserves_writer_error(self, writer: FSClient) -> None:
        cause = OSError("source read failed")
        failure = errors.AccessFailed(path=writer.root / "source", reason=str(cause)).with_cause(cause)

        def fail(destination: BinaryIO) -> Result[None]:
            return Err([failure])

        result = writer.write_stream(ProjectPathId("@/target"), fail)

        assert result.unwrap_err() == [failure]
        assert result.unwrap_err()[0].cause is cause

    def test_write_stream__retains_open_failure(
        self, writer: FSClient, write_source: FileWriter, mocker: MockerFixture
    ) -> None:
        cause = OSError("cannot open destination")
        mocker.patch("pathlib.Path.open", side_effect=cause)

        problem = writer.write_stream(ProjectPathId("@/target"), write_source).unwrap_err()[0]

        assert isinstance(problem, errors.AccessFailed)
        assert problem.path == writer.root / "target"
        assert problem.cause is cause

    @pytest.mark.parametrize("kind", [ContentKind.file, ContentKind.directory, ContentKind.absent])
    def test_remove__removes_selected_root_and_preserves_parent(self, writer: FSClient, kind: ContentKind) -> None:
        target = writer.root / "target"
        (writer.root / "sibling").write_bytes(b"keep")
        if kind == ContentKind.file:
            target.write_bytes(b"remove")
        elif kind == ContentKind.directory:
            (target / "nested").mkdir(parents=True)
            (target / "empty").mkdir()
            (target / "nested" / ".hidden").write_bytes(b"remove")

        writer.remove(ProjectPathId("@/target")).unwrap()

        assert not target.exists()
        assert writer.root.is_dir()
        assert (writer.root / "sibling").read_bytes() == b"keep"

    @pytest.mark.parametrize("directory", [False, True])
    @pytest.mark.parametrize("preserve_root", [False, True])
    def test_remove__readonly_prevents_effects(self, client: FSClient, directory: bool, preserve_root: bool) -> None:
        target = client.root / "target"
        if directory:
            target.mkdir()
            target = target / "file"
        target.write_bytes(b"keep")

        assert client.remove(ProjectPathId("@/target"), preserve_root=preserve_root).is_err(errors.ReadOnly)
        assert target.read_bytes() == b"keep"

    @pytest.mark.parametrize("selected", ["@/", "@/target"])
    def test_remove__preserves_only_selected_directory(self, writer: FSClient, selected: str) -> None:
        target = writer.root if selected == "@/" else writer.root / "target"
        (target / "nested/deeper").mkdir(parents=True)
        (target / "empty").mkdir()
        (target / "nested/deeper/.hidden").write_bytes(b"remove")
        (target / "file").write_bytes(b"remove")
        original = target.stat()

        writer.remove(ProjectPathId(selected), preserve_root=True).unwrap()

        assert target.is_dir() and not tuple(target.iterdir())
        assert target.stat().st_ino == original.st_ino
        assert target.stat().st_mode == original.st_mode

    @pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
    def test_remove__preserves_paths_and_their_ancestors_without_traversal(self, writer: FSClient, kind: str) -> None:
        target = writer.root / "target"
        (target / "nested").mkdir(parents=True)
        preserved = target / "nested/keep"
        if kind == "directory":
            preserved.mkdir()
            (preserved / "link").symlink_to("missing")
        elif kind == "file":
            preserved.write_bytes(b"preserve")
        else:
            preserved.symlink_to("missing")
        before = preserved.lstat()
        (target / "nested/remove").write_bytes(b"remove")
        (target / "remove").write_bytes(b"remove")
        (target / "empty").mkdir()

        writer.remove(
            ProjectPathId("@/target"), preserve_paths=frozenset({ProjectPathId("@/target/nested/keep")})
        ).unwrap()

        assert list(target.iterdir()) == [target / "nested"]
        assert list((target / "nested").iterdir()) == [preserved]
        assert preserved.lstat().st_ino == before.st_ino
        assert preserved.lstat().st_mode == before.st_mode
        if kind == "file":
            assert preserved.read_bytes() == b"preserve"
        elif kind == "directory":
            assert (preserved / "link").is_symlink()

    @pytest.mark.parametrize("exists", [False, True])
    def test_remove__preserve_root_still_removes_files_without_creating_missing_targets(
        self, writer: FSClient, exists: bool
    ) -> None:
        target = writer.root / "target"
        if exists:
            target.write_bytes(b"remove")

        writer.remove(ProjectPathId("@/target"), preserve_root=True).unwrap()

        assert not target.exists()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symbolic links")
    @pytest.mark.parametrize("preserve_root", [False, True])
    def test_remove__unlinks_descendant_symlinks_without_touching_their_targets(
        self, writer: FSClient, tmp_path: Path, preserve_root: bool
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "keep").write_bytes(b"keep")
        target = writer.root / "target"
        (target / "nested").mkdir(parents=True)
        (target / "nested/link").symlink_to(outside, target_is_directory=True)
        (target / "dangling").symlink_to(tmp_path / "missing")

        writer.remove(ProjectPathId("@/target"), preserve_root=preserve_root).unwrap()

        assert (outside / "keep").read_bytes() == b"keep"
        assert target.exists() == preserve_root
        if preserve_root:
            assert not tuple(target.iterdir())

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symbolic links")
    def test_remove__rejects_symlink_root(self, writer: FSClient) -> None:
        target = writer.root / "target"
        target.mkdir()
        (target / "keep").write_bytes(b"keep")
        (writer.root / "link").symlink_to("target")

        assert writer.remove(ProjectPathId("@/link")).is_err(InvalidProjectPath)
        assert (target / "keep").read_bytes() == b"keep"

    @pytest.mark.parametrize("directory", [False, True])
    def test_remove__retains_failure(self, writer: FSClient, mocker: MockerFixture, directory: bool) -> None:
        target = writer.root / "target"
        if directory:
            target.mkdir()
        else:
            target.write_bytes(b"keep")
        cause = OSError("cannot remove")
        mocker.patch("pathlib.Path.rmdir" if directory else "pathlib.Path.unlink", side_effect=cause)

        problem = writer.remove(ProjectPathId("@/target")).unwrap_err()[0]

        assert isinstance(problem, errors.AccessFailed)
        assert problem.path == target
        assert problem.cause is cause
        assert target.exists()

    @pytest.mark.parametrize("preserve_root", [False, True])
    @pytest.mark.parametrize("failure", ["iteration", "child_removal"])
    def test_remove__retains_recursive_failure(
        self, writer: FSClient, mocker: MockerFixture, preserve_root: bool, failure: str
    ) -> None:
        target = writer.root / "target"
        (target / "nested").mkdir(parents=True)
        (target / "nested/file").write_bytes(b"keep")
        cause = PermissionError("Cannot remove selected contents")
        mocker.patch("pathlib.Path.iterdir" if failure == "iteration" else "pathlib.Path.unlink", side_effect=cause)

        result = writer.remove(ProjectPathId("@/target"), preserve_root=preserve_root)

        assert result.is_err(errors.AccessFailed)
        problem = result.unwrap_err()[0]
        assert isinstance(problem, errors.AccessFailed)
        assert problem.path == target and problem.cause is cause
        assert (target / "nested/file").read_bytes() == b"keep"

    @pytest.mark.parametrize("operation", ["write_stream", "remove"])
    def test_mutations__reject_paths_outside_root(
        self, writer: FSClient, write_source: FileWriter, tmp_path: Path, operation: str
    ) -> None:
        outside = tmp_path / "outside"
        outside.write_bytes(b"keep")
        path = ProjectPathId("@/../outside")

        result = writer.write_stream(path, write_source) if operation == "write_stream" else writer.remove(path)

        assert result.is_err(InvalidProjectPath)
        assert outside.read_bytes() == b"keep"
