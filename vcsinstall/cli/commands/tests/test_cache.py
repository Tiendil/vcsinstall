import json
from pathlib import Path

import pytest
import tomli_w
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core import settings
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cache import active_cache_root, errors
from vcsinstall.cache.tests.helpers import cached_repository
from vcsinstall.cli.application import app
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import active_workspace


@pytest.mark.usefixtures("isolated_settings")
class TestClean:
    @pytest.fixture(autouse=True)
    def initialize_settings(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    @pytest.mark.parametrize("state", ["absent", "empty", "populated"])
    def test_results_and_project_independence(
        self, tmp_path: Path, mocker: MockerFixture, protocol: str, state: str
    ) -> None:
        root = tmp_path / "cache"
        if state == "populated":
            cached_repository(root)
        elif state == "empty":
            root.mkdir()
        config = tmp_path / "vcsinstall.toml"
        config.write_bytes(b"malformed[")
        lockfile = tmp_path / "vcsinstall.lock"
        lockfile.write_bytes(b"also malformed[")
        mocker.patch("subprocess.run", side_effect=AssertionError("Cleanup must not invoke external clients."))

        result = CliRunner().invoke(
            app, ["-p", protocol, "--config", str(config), "--cache-dir", str(root), "cache", "clean"]
        )

        assert result.exit_code == 0, result.output
        assert not result.stderr
        outcome = "cleaned" if state == "populated" else "unchanged"
        if protocol == "automation":
            records = [json.loads(line) for line in result.stdout.splitlines()]
            assert len(records) == 1
            record = records[0]
            record.pop("id")
            assert record == {
                "type": "cache",
                "operation": "clean",
                "cache_dir": str(root),
                "outcome": outcome,
                "content": None,
            }
        else:
            assert outcome in result.stdout
            assert str(root) in result.stdout
            if protocol == "llm":
                assert "--VCSINSTALL-CELL " in result.stdout
        assert config.read_bytes() == b"malformed["
        assert lockfile.read_bytes() == b"also malformed["
        assert root.exists() == (state != "absent")
        assert not active_cache_root.is_set()
        assert not active_workspace.is_set()
        assert not project_config.is_set()

    def test_default_cache_location_ignores_a_missing_config(self, tmp_path: Path, mocker: MockerFixture) -> None:
        root = tmp_path / "cache"
        cached_repository(root)
        mocker.patch("vcsinstall.cache.paths.user_cache_path", return_value=root)

        result = CliRunner().invoke(
            app, ["-p", "automation", "--config", str(tmp_path / "missing" / "config"), "cache", "clean"]
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["cache_dir"] == str(root)
        assert json.loads(result.stdout)["outcome"] == "cleaned"
        assert not (tmp_path / "missing").exists()

    def test_relative_cache_path_resolves_from_invocation_directory(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        cwd = tmp_path / "work"
        cwd.mkdir()
        root = tmp_path / "cache"
        cached_repository(root)
        mocker.patch("pathlib.Path.cwd", return_value=cwd)

        result = CliRunner().invoke(app, ["-p", "automation", "--cache-dir", "../cache", "cache", "clean"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["cache_dir"] == str(root)
        assert json.loads(result.stdout)["outcome"] == "cleaned"
        assert not (cwd / "vcsinstall.toml").exists()

    @pytest.mark.parametrize("arguments", [["repository"], ["@/entry"], ["--force"], ["--cache-dir", "other"]])
    def test_rejects_arguments_and_options_before_cache_access(
        self, tmp_path: Path, mocker: MockerFixture, arguments: list[str]
    ) -> None:
        mocker.patch("vcsinstall.cli.preparation.resolve_cache_root", side_effect=AssertionError("Unexpected access"))

        result = CliRunner().invoke(app, ["--cache-dir", str(tmp_path / "cache"), "cache", "clean", *arguments])

        assert result.exit_code == 2
        assert not (tmp_path / "cache").exists()

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_unusable_explicit_root_fails_without_falling_back(
        self, tmp_path: Path, mocker: MockerFixture, protocol: str
    ) -> None:
        root = tmp_path / "cache"
        root.write_bytes(b"unrelated")
        mocker.patch("vcsinstall.cache.paths.user_cache_path", side_effect=AssertionError("Unexpected fallback"))

        result = CliRunner().invoke(app, ["-p", protocol, "--cache-dir", str(root), "cache", "clean"])

        assert result.exit_code == 3
        assert not result.stderr
        assert root.read_bytes() == b"unrelated"
        if protocol == "automation":
            record = json.loads(result.stdout)
            assert record["type"] == "error"
            assert record["code"] == errors.AccessFailed.model_fields["code"].default
            assert record["path"] == str(root)
        else:
            assert str(root) in result.stdout
        assert not active_cache_root.is_set()

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_deletion_failure_reports_partial_cleanup_without_success(
        self, tmp_path: Path, mocker: MockerFixture, protocol: str
    ) -> None:
        root = tmp_path / "cache"
        entry = cached_repository(root)
        cause = PermissionError(13, "Cannot delete cached files", str(entry / "repository"))
        mocker.patch("vcsinstall.cache.storage.shutil.rmtree", side_effect=cause)

        result = CliRunner().invoke(app, ["-p", protocol, "--cache-dir", str(root), "cache", "clean"])

        assert result.exit_code == 3
        assert not result.stderr
        if protocol == "automation":
            record = json.loads(result.stdout)
            assert record["type"] == "error"
            assert record["code"] == errors.CleanupFailed.model_fields["code"].default
            assert record["path"] == str(root)
            assert record["entry"] == str(entry)
        else:
            assert str(root) in result.stdout
        assert "partially" in result.stdout
        assert (entry / "identity.json").is_file()
        assert not active_cache_root.is_set()

    def test_locked_install_rebuilds_cache_without_advancing_revisions(self, tmp_path: Path) -> None:
        client = create_repository(tmp_path / "source")
        root = tmp_path / "cache"
        project = tmp_path / "project"
        project.mkdir()
        config = project / "vcsinstall.toml"
        config.write_text(
            tomli_w.dumps(
                {
                    "version": 1,
                    "dependencies": {
                        "ci": {
                            "vcs": "git",
                            "repository": str(client.path),
                            "selector": {"kind": "branch", "value": "trunk"},
                        }
                    },
                    "entries": [{"dependency": "ci", "source_path": "@/guide.txt", "destination": "@/guide"}],
                }
            )
        )
        arguments = ["-p", "automation", "--config", str(config), "--cache-dir", str(root)]
        runner = CliRunner()
        initial = runner.invoke(app, [*arguments, "install"])
        assert initial.exit_code == 0, initial.output
        revision = json.loads(initial.stdout.splitlines()[0])["revision"]
        lockfile = project / "vcsinstall.lock"
        records_before = (config.read_bytes(), lockfile.read_bytes())
        (client.path / "guide.txt").write_bytes(b"new upstream revision")
        client.add(".").unwrap()
        client.commit("Advance source branch").unwrap()

        cleaned = runner.invoke(app, [*arguments, "cache", "clean"])

        assert cleaned.exit_code == 0, cleaned.output
        assert json.loads(cleaned.stdout)["outcome"] == "cleaned"
        assert (config.read_bytes(), lockfile.read_bytes()) == records_before
        assert (project / "guide").read_bytes() == b"original\n"
        assert (client.path / "guide.txt").read_bytes() == b"new upstream revision"
        (project / "guide").unlink()

        restored = runner.invoke(app, [*arguments, "install", "--locked"])

        assert restored.exit_code == 0, restored.output
        assert all(json.loads(line)["revision"] == revision for line in restored.stdout.splitlines())
        assert (project / "guide").read_bytes() == b"original\n"
        assert (config.read_bytes(), lockfile.read_bytes()) == records_before
