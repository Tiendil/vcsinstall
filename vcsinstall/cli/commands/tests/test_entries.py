import json
import os
import tomllib
from pathlib import Path

import pytest
import tomli_w
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err
from llm_tool_cli.paths import errors as path_errors
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cache import active_cache_root
from vcsinstall.cli.application import app
from vcsinstall.cli.commands.tests.helpers import invoke_add, invoke_entry_add
from vcsinstall.filesystem import errors as filesystem_errors
from vcsinstall.filesystem import hashes
from vcsinstall.runtime import errors as runtime_errors
from vcsinstall.vcs_clients import GitClient
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import active_workspace
from vcsinstall.workspace import errors as workspace_errors


@pytest.mark.usefixtures("isolated_settings")
class TestAddEntry:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        path = project / "custom.toml"
        path.write_text("# configuration\nversion = 1\n")
        return path

    @pytest.fixture
    def client(self, config: Path, tmp_path: Path) -> GitClient:
        client = create_repository(tmp_path / "source")
        code, _, output = invoke_add(config, tmp_path / "cache", "ci", str(client.path))
        assert code == 0, output
        return client

    @pytest.mark.parametrize(
        "vcs,kind",
        [
            ("git", "file"),
            ("git", "directory"),
            ("git", "root"),
            ("filesystem", "file"),
            ("filesystem", "directory"),
            ("filesystem", "root"),
            ("filesystem", "empty"),
        ],
    )
    def test_installs_exact_destination_and_records_baseline(  # noqa: CCR001 — Verify each backend's content kinds.
        self, config: Path, tmp_path: Path, vcs: str, kind: str
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guides").mkdir()
        (source / "guides" / ".hidden").write_bytes(b"literal\x00bytes\r\n")
        (source / "guides" / "run").write_bytes(b"#!/bin/sh\n")
        (source / "guides" / "run").chmod(0o755)
        (source / "empty").mkdir()
        if vcs == "git":
            create_repository(source)
        code, dependency, output = invoke_add(config, tmp_path / "cache", "--vcs", vcs, "ci", str(source))
        assert code == 0, output
        selected = {"file": "guides/.hidden", "directory": "guides", "root": "", "empty": "empty"}[kind]

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/" + selected, "@/renamed")

        assert code == 0, output
        assert len(records) == 1
        record = records[0]
        assert record["type"] == "installation"
        assert record["operation"] == "add"
        assert record["dependency"] == "ci"
        assert record["destination"] == "@/renamed"
        assert record["source_path"] == "@/" + selected
        assert record["outcome"] == "installed"
        assert record["status"] == "clean"
        assert record["revision"] == dependency[0]["revision"]
        assert record["content"] is None
        target = config.parent / "renamed"
        if kind == "file":
            assert target.read_bytes() == b"literal\x00bytes\r\n"
        elif kind == "empty":
            assert not target.exists()
        else:
            directory = target / "guides" if kind == "root" else target
            assert (directory / ".hidden").read_bytes() == b"literal\x00bytes\r\n"
            assert (directory / "run").stat().st_mode & 0o111
        declaration = tomllib.loads(config.read_text())["entries"]
        baseline = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"]
        assert len(declaration) == len(baseline) == 1
        assert declaration[0] == {
            "dependency": "ci",
            "source_path": "@/" + selected,
            "destination": "@/renamed",
        }
        assert baseline[0]["kind"] == ("file" if kind == "file" else "directory")
        if kind == "empty":
            assert baseline[0]["files"] == []
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()
        assert (tmp_path / "cache").exists() == (vcs == "git")

    @pytest.mark.parametrize("form", ["identifier", "relative", "absolute"])
    def test_destination_normalization_uses_invocation_directory(
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture, form: str
    ) -> None:
        invocation = config.parent / "nested"
        invocation.mkdir()
        mocker.patch("pathlib.Path.cwd", return_value=invocation)
        destination = {
            "identifier": " @/nested/unused/../installed ",
            "relative": " ./unused/../installed ",
            "absolute": str(invocation / "installed"),
        }[form]

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", destination)

        assert code == 0, output
        assert records[0]["destination"] == "@/nested/installed"
        assert (invocation / "installed").read_bytes() == b"original\n"

    @pytest.mark.parametrize("name", ["notes%20+draft#1?.md", "@/notes", "Заметки.md", "-guide"])
    def test_preserves_literal_source_names(self, config: Path, tmp_path: Path, name: str) -> None:
        source = tmp_path / "source"
        path = source / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b"literal")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/" + name, "@/installed")

        assert code == 0, output
        assert records[0]["source_path"] == "@/" + name
        assert (config.parent / "installed").read_bytes() == b"literal"

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_results_and_conflicts_use_shared_protocol(
        self, config: Path, client: GitClient, tmp_path: Path, protocol: str
    ) -> None:
        arguments = [
            "-p",
            protocol,
            "--config",
            str(config),
            "--cache-dir",
            str(tmp_path / "cache"),
            "add",
            "ci@/guide.txt",
            "@/installed",
        ]
        success = CliRunner().invoke(app, arguments)
        duplicate = CliRunner().invoke(app, arguments)

        assert success.exit_code == 0, success.output
        assert duplicate.exit_code == 1, duplicate.output
        assert not success.stderr and not duplicate.stderr
        assert "--redefine" in duplicate.stdout
        assert "@/installed" in duplicate.stdout
        if protocol == "llm":
            assert "--VCSINSTALL-CELL " in success.stdout
            assert "--VCSINSTALL-CELL " in duplicate.stdout
        if protocol == "automation":
            assert json.loads(success.stdout)["operation"] == "add"
            assert json.loads(duplicate.stdout)["type"] == "error"

    @pytest.mark.parametrize(
        "arguments",
        [
            [],
            ["ci@/"],
            ["ci@/", "@/one", "@/two"],
            ["--vcs", "git", "ci@/", "@/one"],
            ["--branch", "trunk", "ci@/", "@/one"],
            ["--tag", "v1", "ci@/", "@/one"],
            ["--rev", "1234", "ci@/", "@/one"],
            ["--replace", "ci@/", "@/one"],
            ["git+https://example.test/shared", "@/one"],
            ["ci@/../escape", "@/one"],
        ],
    )
    def test_invalid_command_shape_precedes_project_loading(self, tmp_path: Path, arguments: list[str]) -> None:
        result = CliRunner().invoke(app, ["--config", str(tmp_path / "missing"), "add", *arguments])

        assert result.exit_code == 2, result.output
        assert not tuple(tmp_path.iterdir())
        assert not active_workspace.is_set()

    @pytest.mark.parametrize("state", ["unknown", "missing_lockfile", "missing_lock", "repository", "selector", "vcs"])
    def test_invalid_selection_fails_before_acquisition(  # noqa: CCR001 — Prepare each independent invalid lock state.
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture, state: str
    ) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        source = "missing@/guide.txt" if state == "unknown" else "ci@/guide.txt"
        if state == "missing_lockfile":
            lockfile.unlink()
        elif state == "missing_lock":
            lockfile.write_text("version = 1\n")
        elif state in ("repository", "selector", "vcs"):
            data = tomllib.loads(config.read_text())
            if state == "repository":
                data["dependencies"]["ci"]["repository"] = "other-source"
            elif state == "selector":
                data["dependencies"]["ci"]["selector"] = {"kind": "branch", "value": "other"}
            else:
                data["dependencies"]["ci"] = {"vcs": "filesystem", "repository": str(client.path)}
            config.write_text(tomli_w.dumps(data))
        before = config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None
        acquire = mocker.patch(
            "vcsinstall.hosting.adapters.git.GitHostingAdapter.acquire",
            side_effect=AssertionError("acquisition before validation"),
        )

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "--allow-outdated", source, "@/installed")

        assert code == (1 if state == "unknown" else 3), output
        if state != "unknown":
            assert records[0]["code"] == workspace_errors.DependencyLockRequired.model_fields["code"].default
            assert "install" in str(records[0]["content"])
            assert str(lockfile) in str(records[0]["content"])
        acquire.assert_not_called()
        assert (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None) == before
        assert not (config.parent / "installed").exists()

    @pytest.mark.parametrize(
        "destination", ["@/", "@/custom.toml", "@/vcsinstall.lock", "@/custom.toml/child", "../outside"]
    )
    def test_reserved_or_escaping_destinations_preserve_project(
        self, config: Path, client: GitClient, tmp_path: Path, destination: str
    ) -> None:
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()
        code, _, _ = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", destination)

        assert code != 0
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    @pytest.mark.parametrize("existing", [b"original\n", b"local change"])
    def test_unmanaged_file_must_match_selected_source(
        self, config: Path, client: GitClient, tmp_path: Path, existing: bytes
    ) -> None:
        installed = config.parent / "installed"
        installed.write_bytes(existing)
        os.utime(installed, ns=(1, 1))
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", "@/installed")

        assert code == (0 if existing == b"original\n" else 3), output
        assert installed.read_bytes() == existing
        assert installed.stat().st_mtime_ns == 1
        if code:
            assert records[0]["code"] == runtime_errors.LocalConflict.model_fields["code"].default
            assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    @pytest.mark.parametrize("change", ["missing", "local", "addition", "unchanged"])
    def test_redefinition_checks_complete_local_tree_and_restores_missing_files(  # noqa: CCR001 — Local tree cases.
        self, config: Path, client: GitClient, tmp_path: Path, change: str
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_entry_add(config, cache, "ci@/", "@/installed")[0] == 0
        target = config.parent / "installed" / "guide.txt"
        if change == "missing":
            target.unlink()
        elif change == "local":
            target.write_bytes(b"edited")
        elif change == "addition":
            (target.parent / "generated").write_bytes(b"extra")
        else:
            os.utime(target, ns=(1, 1))
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, cache, "--redefine", "ci@/", "@/installed")

        assert code == (3 if change in ("local", "addition") else 0), output
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        if code == 0:
            assert records[0]["outcome"] == ("unchanged" if change == "unchanged" else "installed")
            assert target.read_bytes() == b"original\n"
            if change == "unchanged":
                assert target.stat().st_mtime_ns == 1

    def test_redefinition_can_change_dependency_and_file_kind(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/unselected")[0] == 0
        original = (config.parent / "unselected").read_bytes()
        source = tmp_path / "other"
        source.mkdir()
        (source / "new").write_bytes(b"new dependency")
        assert invoke_add(config, cache, "other", source.as_uri())[0] == 0
        locks = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["dependencies"]

        code, records, output = invoke_entry_add(config, cache, "--redefine", "other@/", "@/installed")

        assert code == 0, output
        assert records[0]["dependency"] == "other"
        assert (config.parent / "installed" / "new").read_bytes() == b"new dependency"
        assert (config.parent / "unselected").read_bytes() == original
        assert tomllib.loads(config.with_name("vcsinstall.lock").read_text())["dependencies"] == locks
        assert len(tomllib.loads(config.read_text())["entries"]) == 2
        code, _, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/installed")
        assert code == 0, output
        assert (config.parent / "installed").read_bytes() == b"original\n"

    @pytest.mark.parametrize("destination", ["@/installed", "@/installed/child", "@/parent"])
    def test_duplicate_or_overlapping_entries_fail_before_acquisition(
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture, destination: str
    ) -> None:
        existing = "@/parent/child" if destination == "@/parent" else "@/installed"
        assert invoke_entry_add(config, tmp_path / "cache", "ci@/", existing)[0] == 0
        acquire = mocker.patch("vcsinstall.hosting.adapters.git.GitHostingAdapter.acquire")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", destination)

        assert code == 1, output
        assert destination in str(records[0]["content"])
        acquire.assert_not_called()
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_unchanged_redefinition_preserves_order_and_project_bytes(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        for path in ("@/first", "@/second"):
            assert invoke_entry_add(config, cache, "ci@/guide.txt", path)[0] == 0
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/first")

        assert code == 0, output
        assert records[0]["outcome"] == "unchanged"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_unselected_stale_mapping_and_baseline_remain_untouched(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_entry_add(config, cache, "ci@/", "@/unselected")[0] == 0
        lockfile = config.with_name("vcsinstall.lock")
        before = tomllib.loads(lockfile.read_text())
        data = tomllib.loads(config.read_text())
        data["entries"][0]["destination"] = "@/unselected/changed-mapping"
        config.write_text(tomli_w.dumps(data))

        code, _, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/new")

        assert code == 0, output
        assert tomllib.loads(config.read_text())["entries"][0] == data["entries"][0]
        after = tomllib.loads(lockfile.read_text())
        assert after["entries"][0] == before["entries"][0]
        assert after["dependencies"] == before["dependencies"]
        assert (config.parent / "unselected" / "guide.txt").read_bytes() == b"original\n"
        assert (config.parent / "new").read_bytes() == b"original\n"

    @pytest.mark.parametrize("declared", [False, True])
    def test_unselected_symlinked_destinations_do_not_block_add(
        self, config: Path, client: GitClient, tmp_path: Path, declared: bool
    ) -> None:
        mapping = {"dependency": "ci", "source_path": "@/guide.txt", "destination": "@/unselected/installed"}
        if declared:
            configuration = tomllib.loads(config.read_text())
            configuration["entries"] = [mapping]
            config.write_text(tomli_w.dumps(configuration))
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        baseline = {**mapping, "kind": "absent"}
        recorded["entries"] = [baseline]
        lockfile.write_text(tomli_w.dumps(recorded))
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "installed").write_bytes(b"keep")
        (config.parent / "unselected").symlink_to(outside, target_is_directory=True)

        code, _, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", "@/new")

        assert code == 0, output
        assert tomllib.loads(lockfile.read_text())["entries"][0] == baseline
        assert (outside / "installed").read_bytes() == b"keep"
        assert (config.parent / "new").read_bytes() == b"original\n"

    @pytest.mark.parametrize("selector", ["branch", "tag"])
    @pytest.mark.parametrize("same_content", [False, True])
    def test_freshness_permission_keeps_locked_revision_and_unselected_entries(
        self, config: Path, client: GitClient, tmp_path: Path, selector: str, same_content: bool
    ) -> None:
        cache = tmp_path / "cache"
        old_revision = client.resolve_commit("HEAD").unwrap()
        if selector == "tag":
            client.create_tag("release").unwrap()
            assert invoke_add(config, cache, "--redefine", "--tag", "release", "ci", str(client.path))[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/unselected")[0] == 0
        if not same_content:
            (client.path / "guide.txt").write_bytes(b"new source")
        client.run(["commit", "--allow-empty", "-am", "Advance source"]).unwrap()
        current_revision = client.resolve_commit("HEAD").unwrap()
        if selector == "tag":
            client.run(["tag", "--force", "release", current_revision]).unwrap()
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/installed")

        assert code == 3, output
        assert records[0]["code"] == runtime_errors.OutdatedDependency.model_fields["code"].default
        assert old_revision in output and current_revision in output
        assert "--allow-outdated" in output and "dependency update ci" in output
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        code, records, output = invoke_entry_add(config, cache, "--allow-outdated", "ci@/guide.txt", "@/installed")
        assert code == 0, output
        assert records[0]["revision"] == old_revision
        assert (config.parent / "installed").read_bytes() == b"original\n"
        assert (config.parent / "unselected").read_bytes() == b"original\n"
        assert (
            tomllib.loads(config.with_name("vcsinstall.lock").read_text())["dependencies"]["ci"]["revision"]
            == old_revision
        )

    def test_allow_outdated_does_not_bypass_failed_refresh(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        client.path.rename(tmp_path / "unavailable")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, _, output = invoke_entry_add(
            config, tmp_path / "cache", "--allow-outdated", "ci@/guide.txt", "@/installed"
        )

        assert code == 3, output
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert not (config.parent / "installed").exists()

    def test_omitted_git_selector_retains_locked_branch_and_relative_repository(
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        data = tomllib.loads(config.read_text())
        data["dependencies"]["ci"].pop("selector")
        data["dependencies"]["ci"]["repository"] = "../source"
        config.write_text(tomli_w.dumps(data))
        client.create_branch("new-default", checkout=True).unwrap()
        (client.path / "guide.txt").write_bytes(b"other branch")
        client.commit("Change advertised default", all_tracked=True).unwrap()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        mocker.patch("pathlib.Path.cwd", return_value=elsewhere)
        declaration = config.read_bytes()

        code, records, output = invoke_entry_add(config, tmp_path / "fresh-cache", "ci@/guide.txt", "@/installed")

        assert code == 0, output
        assert records[0]["ref"] == "trunk"
        assert (config.parent / "installed").read_bytes() == b"original\n"
        assert tomllib.loads(config.read_text())["dependencies"] == tomllib.loads(declaration.decode())["dependencies"]

    def test_pinned_revision_uses_available_cache_without_refresh(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        revision = client.resolve_commit("HEAD").unwrap()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "--redefine", "--rev", revision, "ci", str(client.path))[0] == 0
        client.path.rename(tmp_path / "unavailable")

        code, records, output = invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")

        assert code == 0, output
        assert records[0]["revision"] == revision
        assert records[0]["ref_kind"] == "revision"

    @pytest.mark.parametrize("allow_outdated", [False, True])
    def test_absent_source_path_preserves_project(
        self, config: Path, client: GitClient, tmp_path: Path, allow_outdated: bool
    ) -> None:
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()
        code, records, output = invoke_entry_add(
            config, tmp_path / "cache", *(["--allow-outdated"] if allow_outdated else []), "ci@/missing", "@/installed"
        )

        assert code == 3, output
        assert records[0]["code"] == runtime_errors.SourcePathAbsent.model_fields["code"].default
        assert records[0]["source_path"] == "@/missing"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_integrity_mismatch_cannot_rewrite_existing_baseline(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        lock = config.with_name("vcsinstall.lock")
        data = tomllib.loads(lock.read_text())
        data["entries"][0]["hash"] = hashes.hash_file(b"different baseline")
        lock.write_text(tomli_w.dumps(data))
        before = config.read_bytes(), lock.read_bytes()

        code, records, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/installed")

        assert code == 3, output
        assert records[0]["code"] == runtime_errors.SourceIntegrityMismatch.model_fields["code"].default
        assert (config.read_bytes(), lock.read_bytes()) == before
        assert (config.parent / "installed").read_bytes() == b"original\n"

    @pytest.mark.parametrize("location", ["destination", "parent", "source"])
    def test_symbolic_links_are_rejected_without_mutation(self, config: Path, tmp_path: Path, location: str) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide").write_bytes(b"source")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0
        if location == "source":
            (source / "link").symlink_to(source / "guide")
        else:
            (config.parent / "link").symlink_to(tmp_path, target_is_directory=True)
        destination = (
            "@/link/installed" if location == "parent" else "@/link" if location == "destination" else "@/installed"
        )
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(
            config, tmp_path / "cache", "--allow-outdated", "ci@/guide", destination
        )

        assert code == 3, output
        assert records[0]["code"] == path_errors.InvalidProjectPath.model_fields["code"].default
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_copy_failure_reports_partial_installation_and_keeps_project_records(
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from vcsinstall.sources.adapters.git import GitSourceAdapter

        problem = filesystem_errors.AccessFailed(path=config.parent / "installed", reason="write denied")
        mocker.patch.object(GitSourceAdapter, "copy_file", return_value=Err([problem]))
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", "@/installed")

        assert code == 3, output
        assert [record["code"] for record in records] == [
            runtime_errors.InstallationWriteFailed.model_fields["code"].default,
            filesystem_errors.AccessFailed.model_fields["code"].default,
        ]
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()
