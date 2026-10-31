import hashlib
import os
import tomllib
import tracemalloc
from pathlib import Path

import pytest
import tomli_w
from llm_tool_cli.core import errors as core_errors
from llm_tool_cli.core import settings
from llm_tool_cli.core.entities import ExitCode
from llm_tool_cli.core.result import Ok
from pytest_mock import MockerFixture

from vcsinstall.cli.tests.helpers import declare_entry, invoke_add
from vcsinstall.filesystem import hashes
from vcsinstall.vcs_clients import clone_mirror
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import errors as workspace_errors


@pytest.mark.usefixtures("isolated_settings")
class TestDependencyAdd:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        path = tmp_path / "vcsinstall.toml"
        path.write_text("# project comment\nversion = 1\n")
        return path

    @pytest.mark.parametrize("layout", ["git", "bare", "linked", "plain", "nested", "force_fs", "file_url"])
    def test_local_detection_and_explicit_overrides(  # noqa: CCR001 — Prepare each supported native repository layout.
        self, config: Path, tmp_path: Path, layout: str
    ) -> None:
        source = tmp_path / "source"
        if layout == "plain":
            source.mkdir()
            (source / "guide.txt").write_bytes(b"live files")
        else:
            client = create_repository(source)
            if layout == "bare":
                source = tmp_path / "bare"
                clone_mirror(client.repository, source).unwrap()
            elif layout == "linked":
                source = tmp_path / "linked"
                client.run(["worktree", "add", "-b", "linked", "--", str(source)]).unwrap()
            elif layout == "nested":
                source = source / "nested"
                source.mkdir()
                (source / "guide.txt").write_bytes(b"selected directory")
        options = ["--vcs", "filesystem"] if layout == "force_fs" else []
        locator = source.as_uri() if layout == "file_url" else str(source)
        code, records, output = invoke_add(config, tmp_path / "cache", *options, "ci", locator)

        assert code == 0, output
        expected = "git" if layout in ("git", "bare", "linked") else "filesystem"
        assert records[0]["vcs"] == expected
        assert tomllib.loads(config.read_text())["dependencies"]["ci"]["vcs"] == expected
        assert (tmp_path / "cache").exists() == (expected == "git")

    def test_corrupt_git_metadata_does_not_fall_back_to_filesystem(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        (source / ".git").mkdir(parents=True)
        before = config.read_bytes()

        code, records, output = invoke_add(config, tmp_path / "cache", "ci", str(source))

        assert code == 3, output
        assert records[0]["code"] == "vcs_command_failed"
        assert config.read_bytes() == before
        assert not config.with_name("vcsinstall.lock").exists()
        assert not (tmp_path / "cache").exists()

    @pytest.mark.parametrize("kind,value", [("rev", "HEAD~1"), ("branch", "bad..branch"), ("tag", "bad tag")])
    def test_invalid_git_selection_fails_before_acquisition(
        self, config: Path, tmp_path: Path, kind: str, value: str
    ) -> None:
        cache = tmp_path / "cache"
        before = config.read_bytes()

        code, _, output = invoke_add(
            config, cache, "--vcs", "git", "--" + kind, value, "ci", str(tmp_path / "missing-source")
        )

        assert code == ExitCode.invalid_arguments, output
        assert config.read_bytes() == before
        assert not config.with_name("vcsinstall.lock").exists()
        assert not cache.exists()

    def test_plain_directory_detection_does_not_require_git(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide").write_bytes(b"live files")
        native = mocker.patch("vcsinstall.vcs_clients.git.subprocess.run", side_effect=FileNotFoundError("git"))

        code, records, output = invoke_add(config, tmp_path / "cache", "ci", str(source))

        assert code == 0, output
        assert records[0]["vcs"] == "filesystem"
        native.assert_not_called()

    def test_normalization_rejection_allows_the_next_backend(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide").write_bytes(b"filesystem source")
        mocker.patch("vcsinstall.sources.adapters.git.GitSourceAdapter.normalize_repository", return_value=Ok(None))

        code, records, output = invoke_add(config, tmp_path / "cache", "ci", str(source))

        assert code == 0, output
        assert records[0]["vcs"] == "filesystem"
        assert not (tmp_path / "cache").exists()

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("selector", ["default", "branch", "tag", "annotated", "revision", "bare"])
    def test_git_selector_forms(self, config: Path, tmp_path: Path, object_format: str, selector: str) -> None:
        source = tmp_path / "source"
        client = create_repository(source, object_format=object_format)
        revision = client.resolve_commit("HEAD").unwrap()
        client.create_tag("release").unwrap()
        client.create_tag("annotated", message="Release").unwrap()
        fragments = {
            "default": "",
            "branch": "#branch=trunk",
            "tag": "#tag=release",
            "annotated": "#tag=annotated",
            "revision": "#rev=" + revision[:12],
            "bare": "#trunk",
        }
        code, records, output = invoke_add(
            config, tmp_path / "cache", "ci", "git+" + source.as_uri() + fragments[selector]
        )
        assert code == 0, output
        record = records[0]
        assert record["revision"] == revision
        assert record["ref_kind"] == (
            "revision" if selector == "revision" else "tag" if selector in ("tag", "annotated") else "branch"
        )
        declared = tomllib.loads(config.read_text())["dependencies"]["ci"]
        assert declared["repository"] == str(source)
        assert declared["vcs"] == "git"
        assert declared["selector"] == {"kind": record["ref_kind"], "value": record["ref"]}
        if selector == "revision":
            assert declared["selector"]["value"] == revision
        assert not list(source.glob(".vcsinstall*"))

    def test_default_replacement_refreshes_and_reinfers(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        first = client.resolve_commit("HEAD").unwrap()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(source))[0] == 0
        client.create_branch("stable", checkout=True).unwrap()
        (source / "guide.txt").write_text("new\n")
        client.commit("New default", all_tracked=True).unwrap()
        current = client.resolve_commit("HEAD").unwrap()
        code, records, output = invoke_add(config, cache, "--redefine", "ci", str(source))
        assert code == 0, output
        assert records[0]["ref"] == "stable"
        assert records[0]["revision"] == current
        assert records[0]["previous_revision"] == first
        previous_bytes = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        assert invoke_add(config, cache, "--redefine", "ci", str(source))[1][0]["outcome"] == "unchanged"
        assert previous_bytes == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

    def test_unavailable_default_does_not_reuse_cached_head(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(source))[0] == 0
        lock = config.with_name("vcsinstall.lock")
        before = (config.read_bytes(), lock.read_bytes())
        client.run(["checkout", "--detach", client.resolve_commit("HEAD").unwrap(), "--"]).unwrap()

        code, records, output = invoke_add(config, cache, "--redefine", "ci", str(source))

        assert code == 3, output
        assert "default branch" in str(records[0]["content"])
        assert (config.read_bytes(), lock.read_bytes()) == before
        code, records, output = invoke_add(
            config, cache, "--redefine", "ci", "git+" + source.as_uri() + "#branch=trunk"
        )
        assert code == 0, output
        assert records[0]["ref"] == "trunk"

    def test_duplicate_and_option_conflicts_do_not_access_cache(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        cache = tmp_path / "unused-cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        before = config.read_bytes()
        code, records, _ = invoke_add(config, cache, "ci", source.as_uri())
        assert code == 1
        assert "--redefine" in str(records[0]["content"])
        assert config.read_bytes() == before
        assert not cache.exists()

    @pytest.mark.parametrize(
        "repository,options",
        [
            ("https://example.com/shared", ["--vcs", "filesystem"]),
            ("svn+https://example.com/shared", []),
            ("git+https://example.com/shared#", []),
            ("git+https://example.com/shared#branch=main&tag=v1", []),
            ("git+https://example.com/shared#branch=main", ["--tag", "v1"]),
            ("git+https://example.com/shared", ["--vcs", "filesystem"]),
            ("file:///missing", ["--branch", "main"]),
            ("/missing", ["--branch", "main", "--tag", "v1"]),
        ],
    )
    def test_invalid_inputs_preserve_project(
        self, config: Path, tmp_path: Path, repository: str, options: list[str]
    ) -> None:
        before = config.read_bytes()
        cache = tmp_path / "cache"
        code, records, output = invoke_add(config, cache, *options, "ci", repository)
        assert code == 1, output
        assert records[0]["type"] == "error"
        assert config.read_bytes() == before
        assert not config.with_name("vcsinstall.lock").exists()
        assert not cache.exists()

    def test_literal_percent_in_selector(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        client.create_branch("literal%").unwrap()

        code, records, output = invoke_add(
            config, tmp_path / "cache", "ci", "git+" + source.as_uri() + "#branch=literal%"
        )

        assert code == 0, output
        assert records[0]["ref"] == "literal%"
        assert tomllib.loads(config.read_text())["dependencies"]["ci"]["selector"] == {
            "kind": "branch",
            "value": "literal%",
        }

    def test_ambiguous_and_deleted_refs_fail_without_mutation(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        client.create_tag("trunk").unwrap()
        before = config.read_bytes()
        code, records, _ = invoke_add(config, tmp_path / "cache", "ci", "git+" + source.as_uri() + "#trunk")
        assert code == 1
        assert "ambiguous" in str(records[0]["content"])
        assert config.read_bytes() == before
        assert invoke_add(config, tmp_path / "cache", "ci", "git+" + source.as_uri() + "#tag=trunk")[0] == 0
        previous_bytes = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        client.delete_tag("trunk").unwrap()
        code, _, _ = invoke_add(
            config, tmp_path / "cache", "--redefine", "ci", "git+" + source.as_uri() + "#tag=trunk"
        )
        assert code == 3
        assert previous_bytes == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

    @pytest.mark.parametrize("local", ["original\n", "selected\n", "missing", "conflict\n"])
    def test_replacement_protects_and_refreshes_entries(self, config: Path, tmp_path: Path, local: str) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide.txt").write_text("original\n")
        assert invoke_add(config, tmp_path / "unused", "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/guide", files=b"original\n")
        lock = config.with_name("vcsinstall.lock")
        target = tmp_path / "guide"
        if local != "missing":
            target.write_text(local)
        (source / "guide.txt").write_text("selected\n")
        before = (config.read_bytes(), lock.read_bytes())
        code, records, output = invoke_add(config, tmp_path / "unused", "--redefine", "ci", source.as_uri())
        if local == "conflict\n":
            assert code == 3, output
            assert before == (config.read_bytes(), lock.read_bytes())
            assert target.read_text() == local
        else:
            assert code == 0, output
            assert [record["type"] for record in records] == ["dependency", "installation"]
            assert records[1]["status"] == "clean"
            assert target.read_text() == "selected\n"
            assert tomllib.loads(lock.read_text())["entries"][0]["hash"] == hashes.hash_file(b"selected\n")

    def test_relative_repository_uses_invocation_directory(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        nested = tmp_path / "nested"
        nested.mkdir()
        source = nested / "source"
        create_repository(source)
        mocker.patch("pathlib.Path.cwd", return_value=nested)
        code, records, output = invoke_add(config, tmp_path / "cache", "ci", "source")
        assert code == 0, output
        assert records[0]["repository"] == str(source)

    @pytest.mark.parametrize("local_change", ["modified", "added"])
    def test_directory_refresh_protects_all_local_files(self, config: Path, tmp_path: Path, local_change: str) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "a").write_bytes(b"old a")
        (source / "b").write_bytes(b"old b")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files={"a": b"old a", "b": b"old b"})
        installed = tmp_path / "installed"
        installed.mkdir()
        local_a = b"local a" if local_change == "modified" else b"old a"
        (installed / "a").write_bytes(local_a)
        if local_change == "added":
            (installed / "addition").write_bytes(b"keep")
        (source / "a").write_bytes(b"new a")
        (source / "b").unlink()
        (source / "new").write_bytes(b"added")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        code, _, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 3, output
        assert before == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        assert (installed / "a").read_bytes() == local_a
        assert not (installed / "new").exists()
        if local_change == "added":
            assert (installed / "addition").read_bytes() == b"keep"

    @pytest.mark.parametrize("vcs", ["filesystem", "git"])
    def test_replacement_applies_each_entry_source_and_destination(
        self, config: Path, tmp_path: Path, vcs: str
    ) -> None:
        source = tmp_path / "source"
        client = create_repository(source) if vcs == "git" else None
        if client is None:
            source.mkdir()
            (source / "guide.txt").write_bytes(b"original\n")
        (source / "bundle" / "nested").mkdir(parents=True)
        (source / "bundle" / "nested" / "guide").write_bytes(b"bundle old")
        if client is not None:
            client.add(".").unwrap()
            client.commit("Initial bundle").unwrap()
        locator = "git+" + source.as_uri() if client is not None else source.as_uri()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", locator)[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed-file", files=b"original\n")
        declare_entry(config, source="@/bundle", destination="@/installed-dir", files={"nested/guide": b"bundle old"})
        (tmp_path / "installed-file").write_bytes(b"original\n")
        installed = tmp_path / "installed-dir"
        (installed / "nested").mkdir(parents=True)
        (installed / "nested" / "guide").write_bytes(b"bundle old")
        (source / "guide.txt").write_bytes(b"new standalone")
        (source / "bundle" / "nested" / "guide").write_bytes(b"new nested")
        (source / "bundle" / "nested" / "added").write_bytes(b"added nested")
        if client is not None:
            client.add(".").unwrap()
            client.commit("Update both entries").unwrap()

        code, records, output = invoke_add(config, cache, "--redefine", "ci", locator)

        assert code == 0, output
        assert [record["type"] for record in records] == ["dependency", "installation", "installation"]
        assert (tmp_path / "installed-file").read_bytes() == b"new standalone"
        assert (installed / "nested" / "guide").read_bytes() == b"new nested"
        assert (installed / "nested" / "added").read_bytes() == b"added nested"
        baselines = {
            entry["destination"]: entry
            for entry in tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"]
        }
        assert baselines["@/installed-file"]["source_path"] == "@/guide.txt"
        assert baselines["@/installed-dir"]["source_path"] == "@/bundle"

    @pytest.mark.parametrize(
        "change,error_type",
        [
            ("removed", workspace_errors.OrphanedBaseline),
            ("destination", workspace_errors.OrphanedBaseline),
            ("entry_owner", workspace_errors.OrphanedBaseline),
            ("baseline_owner", workspace_errors.MissingBaseline),
        ],
    )
    def test_redefinition_rejects_removed_or_reassigned_baselines(
        self, config: Path, tmp_path: Path, change: str, error_type: type[core_errors.EnvironmentError]
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide.txt").write_bytes(b"original")
        cache = tmp_path / "cache"
        for name in ("ci", "formatting"):
            assert invoke_add(config, cache, name, source.as_uri())[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed", files=b"original")
        installed = tmp_path / "installed"
        installed.write_bytes(b"original")
        lock = config.with_name("vcsinstall.lock")
        path = lock if change == "baseline_owner" else config
        data = tomllib.loads(path.read_text())
        if change == "removed":
            data["entries"] = []
        elif change == "destination":
            data["entries"][0]["destination"] = "@/moved"
        else:
            data["entries"][0]["dependency"] = "formatting"
        path.write_text(tomli_w.dumps(data))
        before = (config.read_bytes(), lock.read_bytes())

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == ExitCode.environment_error, output
        assert records[0]["code"] == error_type.model_fields["code"].default
        assert records[0]["dependency"] == "ci"
        assert records[0]["destination"] == "@/installed"
        assert records[0]["path"] == str(lock)
        for guidance in error_type.model_fields["ways_to_fix"].default:
            assert guidance in str(records[0]["content"])
        assert before == (config.read_bytes(), lock.read_bytes())
        assert installed.read_bytes() == b"original"

    def test_whole_selection_is_checked_before_writes(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        for name in ("a", "b"):
            (source / name).write_bytes(b"old")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0
        for name in ("a", "b"):
            declare_entry(config, source="@/" + name, destination="@/" + name, files=b"old")
            (tmp_path / name).write_bytes(b"old" if name == "a" else b"local edit")
            (source / name).write_bytes(b"new")
        before = config.with_name("vcsinstall.lock").read_bytes()
        code, _, output = invoke_add(config, tmp_path / "cache", "--redefine", "ci", source.as_uri())
        assert code == 3, output
        assert (tmp_path / "a").read_bytes() == b"old"
        assert (tmp_path / "b").read_bytes() == b"local edit"
        assert config.with_name("vcsinstall.lock").read_bytes() == before

    @pytest.mark.parametrize("addition", [False, True])
    def test_directory_to_file_protects_local_additions(self, config: Path, tmp_path: Path, addition: bool) -> None:
        source = tmp_path / "source"
        source.mkdir()
        selected = source / "selected"
        selected.mkdir()
        (selected / "old").write_bytes(b"old")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/selected", destination="@/installed", files={"old": b"old"})
        installed = tmp_path / "installed"
        installed.mkdir()
        (installed / "old").write_bytes(b"old")
        if addition:
            (installed / "keep").write_bytes(b"keep")
        (selected / "old").unlink()
        selected.rmdir()
        selected.write_bytes(b"replacement")
        code, _, output = invoke_add(config, tmp_path / "cache", "--redefine", "ci", source.as_uri())
        if addition:
            assert code == 3, output
            assert (installed / "old").read_bytes() == b"old"
            assert (installed / "keep").read_bytes() == b"keep"
        else:
            assert code == 0, output
            assert installed.read_bytes() == b"replacement"

    @pytest.mark.parametrize("backend", ["filesystem", "git"])
    @pytest.mark.parametrize("nested", [False, True])
    @pytest.mark.parametrize("obstruction", ["empty", "empty_descendants", "local_file"])
    def test_selected_file_replaces_directory_obstructions(  # noqa: CCR001 — Backend, mapping and obstruction cases.
        self, config: Path, tmp_path: Path, backend: str, nested: bool, obstruction: str
    ) -> None:
        source = tmp_path / "source"
        client = create_repository(source) if backend == "git" else None
        source.mkdir(exist_ok=True)
        selection = source / "bundle" if nested else source
        selection.mkdir(exist_ok=True)
        selected = selection / "guide.txt"
        selected.write_bytes(b"original\n")
        if client is not None and nested:
            client.add(".").unwrap()
            client.commit("Initial mapping", all_tracked=True).unwrap()
        locator = str(source) if backend == "git" else source.as_uri()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", locator)[0] == 0
        declare_entry(
            config,
            source="@/bundle" if nested else "@/guide.txt",
            destination="@/installed",
            files={"guide.txt": b"original\n"} if nested else b"original\n",
        )
        installed = tmp_path / "installed"
        target = installed / "guide.txt" if nested else installed
        target.mkdir(parents=True)
        if obstruction == "empty_descendants":
            (target / "empty" / "nested").mkdir(parents=True)
        elif obstruction == "local_file":
            (target / "keep").write_bytes(b"local addition")
        selected.write_bytes(b"selected\n")
        if client is not None:
            client.commit("Update mapping", all_tracked=True).unwrap()
        lock_path = config.with_name("vcsinstall.lock")
        before = (config.read_bytes(), lock_path.read_bytes())

        code, _, output = invoke_add(config, cache, "--redefine", "ci", locator)

        if obstruction == "local_file":
            assert code == 3, output
            assert (target / "keep").read_bytes() == b"local addition"
            assert before == (config.read_bytes(), lock_path.read_bytes())
        else:
            assert code == 0, output
            assert target.is_file()
            assert target.read_bytes() == b"selected\n"
            baseline = tomllib.loads(lock_path.read_text())["entries"][0]
            assert baseline["kind"] == ("directory" if nested else "file")
            recorded = baseline["files"][0] if nested else baseline
            assert recorded["hash"] == hashes.hash_file(b"selected\n")

    def test_redefinition_replaces_complete_changed_entry(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        files = {
            "keep.txt": b"keep",
            "old.txt": b"old",
            "sub/old.txt": b"nested old",
            "missing.txt": b"missing",
        }
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files=files)
        installed = tmp_path / "installed"
        for name in ("keep.txt", "old.txt", "sub/old.txt"):
            path = installed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
        os.utime(installed / "keep.txt", ns=(1, 1))
        (installed / "empty").mkdir()
        (tmp_path / "outside").write_bytes(b"keep outside")
        for name in ("old.txt", "sub/old.txt", "missing.txt"):
            (source / name).unlink()
        (source / "new.txt").write_bytes(b"new")

        code, _, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 0, output
        assert not (installed / "old.txt").exists()
        assert not (installed / "sub/old.txt").exists()
        assert not (installed / "missing.txt").exists()
        assert not (installed / "empty").exists()
        assert (installed / "keep.txt").read_bytes() == b"keep"
        assert (installed / "keep.txt").stat().st_mtime_ns != 1
        assert (tmp_path / "outside").read_bytes() == b"keep outside"
        assert (installed / "new.txt").read_bytes() == b"new"
        baseline = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"][0]
        assert {file["path"] for file in baseline["files"]} == {"@/keep.txt", "@/new.txt"}

    def test_upstream_deletion_records_expected_absence(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "selected").write_bytes(b"old")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/selected", destination="@/installed", files=b"old")
        (tmp_path / "installed").write_bytes(b"old")
        (source / "selected").unlink()
        code, _, output = invoke_add(config, tmp_path / "cache", "--redefine", "ci", source.as_uri())
        assert code == 0, output
        assert not (tmp_path / "installed").exists()
        baseline = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"][0]
        assert baseline["kind"] == "absent"
        assert "hash" not in baseline
        assert len(tomllib.loads(config.read_text())["entries"]) == 1

    @pytest.mark.parametrize(
        "initial",
        [
            "version = 1\ndependencies = {}\n",
            "version = 1\nentries = []\n",
            'version = 1\n[dependencies.other]\nvcs = "filesystem"\nrepository = "../other"\n'
            '[[entries]]\ndependency = "other"\nsource_path = "@/guide"\ndestination = "@/other"\n',
        ],
    )
    def test_serialization_preserves_existing_declarations(self, config: Path, tmp_path: Path, initial: str) -> None:
        config.write_text(initial)
        source = tmp_path / "source"
        source.mkdir()
        code, _, output = invoke_add(config, tmp_path / "cache", "ci", source.as_uri())
        assert code == 0, output
        written = tomllib.loads(config.read_text())
        original = tomllib.loads(initial)
        assert written["dependencies"]["ci"]["repository"] == str(source)
        for name, dependency in original.get("dependencies", {}).items():
            assert written["dependencies"][name] == dependency
        assert written.get("entries", []) == original.get("entries", [])

    @pytest.mark.parametrize("unsupported", [False, True])
    def test_git_replacement_uses_selected_committed_content(
        self, config: Path, tmp_path: Path, unsupported: bool
    ) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(source))[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed", files=b"original\n")
        installed = tmp_path / "installed"
        installed.write_bytes(b"original\n")
        if unsupported:
            (source / "guide.txt").unlink()
            (source / "guide.txt").symlink_to("outside")
        else:
            (source / "guide.txt").write_bytes(b"committed\x00bytes\r\n")
            (source / "outside").symlink_to("missing")
        client.add(".").unwrap()
        client.commit("Change source").unwrap()
        if not unsupported:
            (source / "guide.txt").write_bytes(b"uncommitted working tree\n")
        index = (source / ".git" / "index").read_bytes()
        previous = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        code, _, output = invoke_add(config, cache, "--redefine", "ci", str(source))
        assert (source / ".git" / "index").read_bytes() == index
        if unsupported:
            assert code == 3, output
            assert previous == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
            assert installed.read_bytes() == b"original\n"
        else:
            assert code == 0, output
            assert installed.read_bytes() == b"committed\x00bytes\r\n"
            assert (source / "guide.txt").read_bytes() == b"uncommitted working tree\n"

    @pytest.mark.parametrize("vcs", ["filesystem", "git"])
    def test_large_file_replacement_uses_bounded_python_memory(self, config: Path, tmp_path: Path, vcs: str) -> None:
        source = tmp_path / "source"
        client = create_repository(source) if vcs == "git" else None
        if client is None:
            source.mkdir()
            (source / "guide.txt").write_bytes(b"original\n")
        locator = "git+" + source.as_uri() if client is not None else source.as_uri()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", locator)[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed", files=b"original\n")
        installed = tmp_path / "installed"
        installed.write_bytes(b"original\n")
        size = 16 * 1024 * 1024
        with (source / "guide.txt").open("wb") as stream:
            stream.truncate(size)
        if client is not None:
            client.commit("Large file", all_tracked=True).unwrap()

        tracemalloc.start()
        try:
            code, _, output = invoke_add(config, cache, "--redefine", "ci", locator)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert code == 0, output
        assert peak < 6 * 1024 * 1024
        assert installed.stat().st_size == size
        with installed.open("rb") as destination, (source / "guide.txt").open("rb") as original:
            assert (
                hashlib.file_digest(destination, "sha256").digest() == hashlib.file_digest(original, "sha256").digest()
            )

    def test_literal_selector_encoding_and_option_equivalence(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        client.create_branch("Feature+%2F").unwrap()
        cache = tmp_path / "cache"
        code, records, output = invoke_add(config, cache, "ci", "git+" + source.as_uri() + "#branch=Feature+%252F")
        assert code == 0, output
        assert records[0]["ref"] == "Feature+%2F"
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        code, records, output = invoke_add(config, cache, "--redefine", "--branch", "Feature+%2F", "ci", str(source))
        assert code == 0, output
        assert records[0]["outcome"] == "unchanged"
        assert before == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

    def test_failed_direct_lockfile_write_reports_partial_state(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from vcsinstall.filesystem import errors as filesystem_errors

        source = tmp_path / "source"
        source.mkdir()
        lock = config.with_name("vcsinstall.lock")
        lock.write_text("# existing lock\nversion = 1\n")
        original_write = Path.write_bytes

        def write_file(path: Path, data: bytes) -> int:
            if path == lock:
                original_write(path, data[:4])
                raise OSError("simulated lockfile write failure")
            return original_write(path, data)

        mocker.patch("pathlib.Path.write_bytes", side_effect=write_file, autospec=True)
        code, records, output = invoke_add(config, tmp_path / "cache", "ci", source.as_uri())
        assert code == 3, output
        assert lock.read_bytes() == b"vers"
        assert "ci" in tomllib.loads(config.read_text())["dependencies"]
        assert records[0]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert records[0]["path"] == str(lock)
        assert str(config) in str(records[0]["reason"])
        assert not list(tmp_path.glob(".vcsinstall*"))

    def test_installation_write_failure_reports_inconsistency_and_original_error(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from llm_tool_cli.core.result import Err

        from vcsinstall.filesystem import FSClient
        from vcsinstall.filesystem import errors as filesystem_errors
        from vcsinstall.runtime import errors as runtime_errors

        source = tmp_path / "source"
        source.mkdir()
        (source / "old").write_bytes(b"old")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files={"old": b"old"})
        installed = tmp_path / "installed"
        installed.mkdir()
        (installed / "old").write_bytes(b"old")
        (source / "old").unlink()
        (source / "new").write_bytes(b"new")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        failure = filesystem_errors.AccessFailed(path=installed / "new", reason="simulated copy failure")
        mocker.patch.object(FSClient, "write_stream", return_value=Err([failure]))

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 3, output
        assert not (installed / "old").exists()
        assert before == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        notice = runtime_errors.InstallationWriteFailed()
        assert records[0]["code"] == notice.code
        assert notice.format_message() in str(records[0]["content"])
        assert records[1]["code"] == failure.code
        assert records[1]["path"] == str(installed / "new")
        content = str(records[1]["content"])
        assert failure.format_message() in content
        assert all(fix in content for fix in failure.ways_to_fix)

    def test_matching_installed_file_is_not_rewritten(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "selected").write_bytes(b"same")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/selected", destination="@/installed", files=b"same")
        installed = tmp_path / "installed"
        installed.write_bytes(b"same")
        timestamp = installed.stat().st_mtime_ns

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 0, output
        assert installed.stat().st_mtime_ns == timestamp
        assert records[0]["outcome"] == "unchanged"

    def test_matching_complete_directory_accepts_local_additions_without_rewriting(
        self, config: Path, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "original").write_bytes(b"same")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files={"original": b"same"})
        installed = tmp_path / "installed"
        installed.mkdir()
        for name, data in (("original", b"same"), ("addition", b"added")):
            (source / name).write_bytes(data)
            (installed / name).write_bytes(data)
            os.utime(installed / name, ns=(1, 1))
        (installed / "empty").mkdir()

        code, _, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 0, output
        assert (installed / "empty").is_dir()
        assert all((installed / name).stat().st_mtime_ns == 1 for name in ("original", "addition"))
        baseline = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"][0]
        assert {file["path"] for file in baseline["files"]} == {"@/original", "@/addition"}

    def test_revision_only_change_does_not_recreate_installed_directory(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        locator = "git+" + source.as_uri()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", locator)[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files={"guide.txt": b"original\n"})
        installed = tmp_path / "installed"
        installed.mkdir()
        (installed / "guide.txt").write_bytes(b"original\n")
        os.utime(installed / "guide.txt", ns=(1, 1))
        (installed / "empty").mkdir()
        lock_path = config.with_name("vcsinstall.lock")
        previous_revision = tomllib.loads(lock_path.read_text())["dependencies"]["ci"]["revision"]
        client.run(["commit", "--allow-empty", "-m", "Advance without content changes"]).unwrap()

        code, records, output = invoke_add(config, cache, "--redefine", "ci", locator)

        assert code == 0, output
        assert records[0]["revision"] != previous_revision
        assert tomllib.loads(lock_path.read_text())["dependencies"]["ci"]["revision"] == records[0]["revision"]
        assert (installed / "empty").is_dir()
        assert (installed / "guide.txt").stat().st_mtime_ns == 1

    def test_installation_failure_keeps_partial_content_and_prior_project_records(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from collections.abc import Callable
        from typing import BinaryIO

        from llm_tool_cli.core.result import Err, Result
        from llm_tool_cli.paths import ProjectPathId

        from vcsinstall.filesystem import FSClient
        from vcsinstall.filesystem import errors as filesystem_errors
        from vcsinstall.runtime import errors as runtime_errors

        source = tmp_path / "source"
        source.mkdir()
        for name in ("a", "b"):
            (source / name).write_bytes(b"old")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        for name in ("a", "b"):
            declare_entry(config, source="@/" + name, destination="@/" + name, files=b"old")
            (tmp_path / name).write_bytes(b"old")
            (source / name).write_bytes(b"new")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        write_stream = FSClient.write_stream

        def fail_second(
            client: FSClient,
            path: ProjectPathId,
            write: Callable[[BinaryIO], Result[None]],
            *,
            executable: bool = False,
        ) -> Result[None]:
            if path == "@/b":
                return Err([filesystem_errors.AccessFailed(path=tmp_path / "b", reason="second write failed")])
            return write_stream(client, path, write, executable=executable)

        mocker.patch.object(FSClient, "write_stream", autospec=True, side_effect=fail_second)

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 3, output
        assert (tmp_path / "a").read_bytes() == b"new"
        assert not (tmp_path / "b").exists()
        assert records[0]["code"] == runtime_errors.InstallationWriteFailed.model_fields["code"].default
        assert records[1]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert records[1]["path"] == str(tmp_path / "b")
        assert before == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
