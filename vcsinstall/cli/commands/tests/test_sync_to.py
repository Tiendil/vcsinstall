import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pytest
import tomli_w
from llm_tool_cli.core import settings
from llm_tool_cli.core.entities import ToolName
from llm_tool_cli.core.result import Err, Result
from llm_tool_cli.paths import ProjectPathId
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cli.application import app
from vcsinstall.cli.commands.tests.helpers import (
    invoke_add,
    invoke_dependency_sync_to,
    invoke_dependency_update,
    invoke_entry_add,
    invoke_entry_list,
)
from vcsinstall.filesystem import FSClient
from vcsinstall.filesystem import errors as filesystem_errors
from vcsinstall.runtime import errors
from vcsinstall.vcs_clients import GitClient
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import errors as workspace_errors


@pytest.fixture(autouse=True)
def initialize_settings(isolated_settings: None) -> None:
    settings.initialize(name=ToolName("vcsinstall"), config_filename=settings.ConfigFilename("vcsinstall.toml"))


@pytest.fixture
def sync_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    (source / "bundle").mkdir(parents=True)
    (source / "guide.txt").write_bytes(b"guide")
    (source / "bundle/a.txt").write_bytes(b"a")
    (source / "bundle/b.txt").write_bytes(b"b")
    project = tmp_path / "project"
    project.mkdir()
    config = project / "vcsinstall.toml"
    config.write_text("version = 1\n")
    cache = tmp_path / "cache"
    assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
    assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed-guide")[0] == 0
    assert invoke_entry_add(config, cache, "ci@/bundle", "@/docs")[0] == 0
    return config, cache, source


class TestSyncDependencyTo:
    def test_changed_entries_mirror_complete_trees_and_preserve_installation_state(
        self, sync_project: tuple[Path, Path, Path]
    ) -> None:
        config, cache, source = sync_project
        (config.parent / "installed-guide").write_bytes(b"edited guide")
        (config.parent / "docs/a.txt").write_bytes(b"edited a")
        (config.parent / "docs/b.txt").unlink()
        (config.parent / "docs/.new").write_bytes(b"new\x00file")
        (source / "outside").write_bytes(b"unrelated")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_dependency_sync_to(config, cache, "ci", str(source))

        assert code == 0, output
        assert [row["destination"] for row in records] == ["@/docs", "@/installed-guide"]
        assert all(row["operation"] == "dependency sync-to" and row["outcome"] == "synced" for row in records)
        assert all(row["source_dir"] == str(source) and row["status"] == "modified" for row in records)
        assert all(row["type"] == "installation" and row["content"] is None for row in records)
        assert (source / "guide.txt").read_bytes() == b"edited guide"
        assert (source / "bundle/a.txt").read_bytes() == b"edited a"
        assert not (source / "bundle/b.txt").exists()
        assert (source / "bundle/.new").read_bytes() == b"new\x00file"
        assert (source / "outside").read_bytes() == b"unrelated"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert not cache.exists()
        assert all(row["status"] == "modified" for row in invoke_entry_list(config)[1])
        code, records, output = invoke_dependency_sync_to(config, cache, "ci", str(source))
        assert code == 0, output
        assert all(row["outcome"] == "unchanged" for row in records)
        assert invoke_dependency_update(config, cache, "ci")[0] == 0
        assert invoke_dependency_sync_to(config, cache, "ci", str(source))[:2] == (0, [])

    @pytest.mark.parametrize("force", [False, True])
    @pytest.mark.parametrize("recipient", ["modified", "missing", "addition"])
    def test_recipient_changes_require_force(
        self, sync_project: tuple[Path, Path, Path], force: bool, recipient: str
    ) -> None:
        config, cache, source = sync_project
        (config.parent / "docs/a.txt").write_bytes(b"local")
        if recipient == "modified":
            (source / "bundle/a.txt").write_bytes(b"upstream")
        elif recipient == "missing":
            (source / "bundle/b.txt").unlink()
        else:
            (source / "bundle/extra").write_bytes(b"upstream addition")
        before = {path.name: path.read_bytes() for path in (source / "bundle").iterdir()}

        code, records, output = invoke_dependency_sync_to(
            config, cache, *("--force",) if force else (), "ci", str(source)
        )

        assert code == (0 if force else 3), output
        files = {path.name: path.read_bytes() for path in (source / "bundle").iterdir()}
        if force:
            assert files == {"a.txt": b"local", "b.txt": b"b"}
        else:
            assert files == before
            assert any(row.get("code") == errors.LocalConflict.model_fields["code"].default for row in records)

    @pytest.mark.parametrize("selection", ["file", "directory"])
    def test_local_deletions_are_returned_to_the_recipient(
        self, sync_project: tuple[Path, Path, Path], selection: str
    ) -> None:
        config, cache, source = sync_project
        if selection == "file":
            (config.parent / "installed-guide").unlink()
            selected = source / "guide.txt"
        else:
            (config.parent / "docs/a.txt").unlink()
            (config.parent / "docs/b.txt").unlink()
            (config.parent / "docs").rmdir()
            selected = source / "bundle"
        before = config.with_name("vcsinstall.lock").read_bytes()
        code, records, output = invoke_dependency_sync_to(config, cache, "ci", str(source))
        assert code == 0, output
        assert records[0]["status"] == "missing"
        assert records[0]["outcome"] == "synced"
        assert not selected.exists()
        assert config.with_name("vcsinstall.lock").read_bytes() == before

    def test_all_recipient_checks_finish_before_any_copy(self, sync_project: tuple[Path, Path, Path]) -> None:
        config, cache, source = sync_project
        (config.parent / "docs/a.txt").write_bytes(b"first change")
        (config.parent / "installed-guide").write_bytes(b"second change")
        (source / "guide.txt").write_bytes(b"recipient conflict")
        code, _, _ = invoke_dependency_sync_to(config, cache, "ci", str(source))
        assert code == 3
        assert (source / "bundle/a.txt").read_bytes() == b"a"
        assert (source / "guide.txt").read_bytes() == b"recipient conflict"

    @pytest.mark.parametrize("force", [False, True])
    def test_missing_baselines_fail_even_with_force(self, sync_project: tuple[Path, Path, Path], force: bool) -> None:
        config, cache, source = sync_project
        lock = config.with_name("vcsinstall.lock")
        data = tomllib.loads(lock.read_text())
        data["entries"] = []
        lock.write_text(tomli_w.dumps(data))
        before = lock.read_bytes()
        code, records, output = invoke_dependency_sync_to(
            config, cache, *("--force",) if force else (), "ci", str(source)
        )
        assert code == 3, output
        assert any(row.get("code") == workspace_errors.MissingBaseline.model_fields["code"].default for row in records)
        assert lock.read_bytes() == before
        assert (source / "guide.txt").read_bytes() == b"guide"

    def test_no_changed_entries_do_not_inspect_unrelated_recipient_objects(
        self, sync_project: tuple[Path, Path, Path]
    ) -> None:
        config, cache, source = sync_project
        (source / "unrelated-link").symlink_to("missing")
        assert invoke_dependency_sync_to(config, cache, "ci", str(source))[:2] == (0, [])
        assert not cache.exists()

    @pytest.mark.parametrize("marker", ["directory", "file", "symlink"])
    def test_root_mapping_preserves_repository_administration(
        self, sync_project: tuple[Path, Path, Path], marker: str
    ) -> None:
        config, cache, source = sync_project
        assert invoke_entry_add(config, cache, "ci@/", "@/whole")[0] == 0
        (config.parent / "whole/guide.txt").write_bytes(b"whole root edit")
        administrative = source / ".git"
        if marker == "directory":
            administrative.mkdir()
            (administrative / "HEAD").write_bytes(b"preserve head")
        elif marker == "file":
            administrative.write_bytes(b"gitdir: /some/shared/worktree\n")
        else:
            administrative.symlink_to("missing-git-storage")
        code, records, output = invoke_dependency_sync_to(config, cache, "ci", str(source))
        assert code == 0, output
        assert records[0]["outcome"] == "synced"
        assert (source / "guide.txt").read_bytes() == b"whole root edit"
        if marker == "directory":
            assert (administrative / "HEAD").read_bytes() == b"preserve head"
        elif marker == "file":
            assert administrative.read_bytes() == b"gitdir: /some/shared/worktree\n"
        else:
            assert administrative.is_symlink()

    @pytest.mark.parametrize("selection", ["file_to_directory", "directory_to_file"])
    def test_complete_replacement_supports_type_changes(
        self, sync_project: tuple[Path, Path, Path], selection: str
    ) -> None:
        config, cache, source = sync_project
        if selection == "file_to_directory":
            local = config.parent / "installed-guide"
            local.unlink()
            local.mkdir()
            (local / "child").write_bytes(b"new child")
            recipient = source / "guide.txt/child"
        else:
            local = config.parent / "docs"
            (local / "a.txt").unlink()
            (local / "b.txt").unlink()
            local.rmdir()
            local.write_bytes(b"new child")
            recipient = source / "bundle"

        code, records, output = invoke_dependency_sync_to(config, cache, "ci", str(source))

        assert code == 0, output
        assert records[0]["outcome"] == "synced"
        assert recipient.read_bytes() == b"new child"

    def test_nested_metadata_survives_deletion_of_its_content_tree(
        self, sync_project: tuple[Path, Path, Path]
    ) -> None:
        config, cache, source = sync_project
        (config.parent / "docs/a.txt").unlink()
        (config.parent / "docs/b.txt").unlink()
        (source / "bundle/nested").mkdir()
        marker = source / "bundle/nested/.GIT"
        marker.symlink_to("missing-storage")

        code, records, output = invoke_dependency_sync_to(config, cache, "ci", str(source))

        assert code == 0, output
        assert records[0]["outcome"] == "synced"
        assert marker.is_symlink()
        assert not (source / "bundle/a.txt").exists()
        assert not (source / "bundle/b.txt").exists()

    @pytest.mark.parametrize("force", [False, True])
    def test_metadata_obstructions_are_rejected_before_writes(
        self, sync_project: tuple[Path, Path, Path], force: bool
    ) -> None:
        config, cache, source = sync_project
        (config.parent / "installed-guide").write_bytes(b"local")
        (source / "guide.txt").unlink()
        (source / "guide.txt/.git").mkdir(parents=True)
        (source / "guide.txt/.git/HEAD").write_bytes(b"keep")
        code, _, _ = invoke_dependency_sync_to(config, cache, *("--force",) if force else (), "ci", str(source))
        assert code == 1
        assert (source / "guide.txt/.git/HEAD").read_bytes() == b"keep"

    @pytest.mark.parametrize("recipient_kind", ["installation", "cache", "administration"])
    def test_force_cannot_write_protected_locations(
        self, sync_project: tuple[Path, Path, Path], recipient_kind: str
    ) -> None:
        config, cache, source = sync_project
        (config.parent / "installed-guide").write_bytes(b"local")
        recipient = (
            config.parent / "docs"
            if recipient_kind == "installation"
            else cache if recipient_kind == "cache" else source / ".git"
        )
        recipient.mkdir(exist_ok=True)
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()
        code, _, _ = invoke_dependency_sync_to(config, cache, "--force", "ci", str(recipient))
        assert code == 1
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert not (recipient / "guide.txt").exists()

    @pytest.mark.parametrize("force", [False, True])
    def test_local_administrative_paths_are_rejected(self, sync_project: tuple[Path, Path, Path], force: bool) -> None:
        config, cache, source = sync_project
        (config.parent / "docs/.gIt").mkdir()
        (config.parent / "docs/.gIt/config").write_bytes(b"local metadata")

        code, _, _ = invoke_dependency_sync_to(config, cache, *("--force",) if force else (), "ci", str(source))

        assert code == 1
        assert not (source / "bundle/.gIt").exists()
        assert (source / "bundle/a.txt").read_bytes() == b"a"

    def test_source_parent_obstructions_are_not_removed(self, sync_project: tuple[Path, Path, Path]) -> None:
        config, cache, source = sync_project
        assert invoke_entry_add(config, cache, "ci@/bundle/a.txt", "@/child")[0] == 0
        (config.parent / "child").write_bytes(b"local child")
        (source / "bundle/a.txt").unlink()
        (source / "bundle/b.txt").unlink()
        (source / "bundle").rmdir()
        (source / "bundle").write_bytes(b"parent obstruction")
        code, _, _ = invoke_dependency_sync_to(config, cache, "--force", "ci", str(source))
        assert code == 3
        assert (source / "bundle").read_bytes() == b"parent obstruction"

    @pytest.mark.parametrize("agree", [False, True])
    def test_overlapping_mappings_require_agreement(
        self, sync_project: tuple[Path, Path, Path], agree: bool, mocker: MockerFixture
    ) -> None:
        config, cache, source = sync_project
        assert invoke_entry_add(config, cache, "ci@/bundle/a.txt", "@/child")[0] == 0
        (config.parent / "docs/a.txt").write_bytes(b"parent proposal")
        (config.parent / "child").write_bytes(b"parent proposal" if agree else b"conflicting proposal")
        remove = mocker.spy(FSClient, "remove")
        code, records, output = invoke_dependency_sync_to(config, cache, "ci", str(source))
        assert code == (0 if agree else 1), output
        if agree:
            assert len(records) == 2
            assert remove.call_count == 1
            assert (source / "bundle/a.txt").read_bytes() == b"parent proposal"
        else:
            remove.assert_not_called()
            assert (source / "bundle/a.txt").read_bytes() == b"a"

    def test_original_git_source_and_cache_are_not_needed(self, tmp_path: Path, mocker: MockerFixture) -> None:
        original = create_repository(tmp_path / "original")
        project = tmp_path / "project"
        project.mkdir()
        config = project / "vcsinstall.toml"
        config.write_text("version = 1\n")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(original.path))[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        recipient = tmp_path / "recipient"
        recipient.mkdir()
        (recipient / "guide.txt").write_bytes(b"original\n")
        (project / "installed").write_bytes(b"local")
        original.path.rename(tmp_path / "unavailable")
        cache.rename(tmp_path / "old-cache")
        mocker.patch.object(GitClient, "run", side_effect=AssertionError("Sync must not invoke Git"))
        code, _, output = invoke_dependency_sync_to(config, cache, "ci", str(recipient))
        assert code == 0, output
        assert (recipient / "guide.txt").read_bytes() == b"local"

    def test_force_replaces_obstructions_inside_compatible_overlapping_mappings(
        self, sync_project: tuple[Path, Path, Path]
    ) -> None:
        config, cache, source = sync_project
        assert invoke_entry_add(config, cache, "ci@/bundle/a.txt", "@/child")[0] == 0
        (config.parent / "docs/a.txt").write_bytes(b"proposal")
        (config.parent / "child").write_bytes(b"proposal")
        (source / "bundle/a.txt").unlink()
        (source / "bundle/b.txt").unlink()
        (source / "bundle").rmdir()
        (source / "bundle").write_bytes(b"recipient obstruction")

        code, records, output = invoke_dependency_sync_to(config, cache, "--force", "ci", str(source))

        assert code == 0, output
        assert len(records) == 2
        assert all(row["outcome"] == "synced" for row in records)
        assert (source / "bundle/a.txt").read_bytes() == b"proposal"
        assert (source / "bundle/b.txt").read_bytes() == b"b"

    def test_unrelated_dependency_content_is_not_inspected(self, sync_project: tuple[Path, Path, Path]) -> None:
        config, cache, source = sync_project
        assert invoke_add(config, cache, "other", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "other@/guide.txt", "@/other")[0] == 0
        (config.parent / "other").unlink()
        (config.parent / "other").symlink_to("missing")
        (config.parent / "installed-guide").write_bytes(b"proposal")

        code, records, output = invoke_dependency_sync_to(config, cache, "ci", str(source))

        assert code == 0, output
        assert len(records) == 1
        assert records[0]["dependency"] == "ci"
        assert (config.parent / "other").is_symlink()
        assert (source / "guide.txt").read_bytes() == b"proposal"
        assert not cache.exists()

    def test_write_failure_reports_partial_effects_without_changing_project_state(
        self, sync_project: tuple[Path, Path, Path], mocker: MockerFixture
    ) -> None:
        config, cache, source = sync_project
        (config.parent / "docs/a.txt").write_bytes(b"first completed")
        (config.parent / "installed-guide").write_bytes(b"second failed")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()
        write = FSClient.write_stream
        problem = filesystem_errors.AccessFailed(path=source / "guide.txt", reason="write failed")

        def fail(
            client: FSClient,
            path: ProjectPathId,
            writer: Callable[[BinaryIO], Result[None]],
            *,
            executable: bool = False,
        ) -> Result[None]:
            if path == "@/guide.txt":
                return Err([problem])
            return write(client, path, writer, executable=executable)

        mocker.patch.object(FSClient, "write_stream", side_effect=fail, autospec=True)
        code, records, _ = invoke_dependency_sync_to(config, cache, "ci", str(source))
        assert code == 3
        assert any(row.get("code") == errors.SyncWriteFailed.model_fields["code"].default for row in records)
        assert (source / "bundle/a.txt").read_bytes() == b"first completed"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert (config.parent / "installed-guide").read_bytes() == b"second failed"

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_protocols_report_local_sync(self, sync_project: tuple[Path, Path, Path], protocol: str) -> None:
        config, cache, source = sync_project
        (config.parent / "installed-guide").write_bytes(b"local")
        result = CliRunner().invoke(
            app,
            [
                "-p",
                protocol,
                "--config",
                str(config),
                "--cache-dir",
                str(cache),
                "dependency",
                "sync-to",
                "ci",
                str(source),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "dependency sync-to" in result.stdout
        assert "source_dir" in result.stdout

    def test_unknown_name_invalid_root_and_old_push_are_rejected(self, sync_project: tuple[Path, Path, Path]) -> None:
        config, cache, source = sync_project
        assert invoke_dependency_sync_to(config, cache, "unknown", str(source))[0] == 1
        assert invoke_dependency_sync_to(config, cache, "ci", str(source / "missing"))[0] == 1
        assert CliRunner().invoke(app, ["dependency", "push", "--help"]).exit_code == 2
