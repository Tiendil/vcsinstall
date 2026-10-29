import json
from pathlib import Path

import pytest
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Result
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cli import application
from vcsinstall.cli.commands import dependency
from vcsinstall.domain import DependencyName, RepositoryLocator
from vcsinstall.sources import errors as source_errors
from vcsinstall.workspace import Workspace, active_workspace
from vcsinstall.workspace import errors as workspace_errors


@pytest.mark.usefixtures("isolated_settings")
class TestAddDependency:
    @pytest.fixture(autouse=True)
    def initialize_settings(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    def test_dependency_add_loads_selected_project_and_is_empty(self, tmp_path: Path, mocker: MockerFixture) -> None:
        target = tmp_path / "custom.toml"
        target.write_text("version = 1\n")
        lock = tmp_path / "vcsinstall.lock"
        lock.write_text("version = 1\n")
        workspaces: list[Workspace] = []
        real_dependency_add = dependency.dependency_add

        def observe_workspace(name: DependencyName, repository: RepositoryLocator) -> Result[None]:
            workspaces.append(active_workspace.get())
            return real_dependency_add(name, repository)

        spy = mocker.patch.object(dependency, "dependency_add", side_effect=observe_workspace)

        for _ in range(2):
            result = CliRunner().invoke(
                application.app,
                ["-p", "automation", "--config", str(target), "dependency", "add", "ci", "unused-source"],
            )

            assert result.exit_code == 0, result.output
            assert not result.output
            assert not project_config.is_set()
            assert not active_workspace.is_set()
        assert spy.call_count == 2
        assert spy.call_args.args == (DependencyName("ci"), RepositoryLocator("unused-source"))
        assert not spy.call_args.kwargs
        assert len(workspaces) == 2
        for workspace in workspaces:
            assert workspace.config_path == target
            assert workspace.lockfile_path == lock
            assert workspace.lockfile is not None
        assert target.read_text() == "version = 1\n"
        assert lock.read_text() == "version = 1\n"

    def test_dependency_add_accepts_noop_project(self, tmp_path: Path) -> None:
        config = tmp_path / "custom.toml"
        declaration = '[dependencies.ci]\nvcs = "noop"\nrepository = "fixture-id"\nbranch = "fixture branch"\n'
        config_text = "version = 1\n" + declaration
        lock_text = config_text + 'revision = "opaque-revision"\n'
        config.write_text(config_text)
        lock = tmp_path / "vcsinstall.lock"
        lock.write_text(lock_text)

        result = CliRunner().invoke(
            application.app, ["-p", "automation", "--config", str(config), "dependency", "add", "ci", "fixture-id"]
        )

        assert result.exit_code == 0, result.output
        assert not result.output
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert config.read_text() == config_text
        assert lock.read_text() == lock_text

    def test_runtime_exception_cleans_up_workspace(self, tmp_path: Path, mocker: MockerFixture) -> None:
        target = tmp_path / "custom.toml"
        target.write_text("version = 1\n")
        mocker.patch.object(dependency, "dependency_add", side_effect=RuntimeError("operation failed"))

        result = CliRunner().invoke(
            application.app, ["--config", str(target), "dependency", "add", "ci", "unused-source"]
        )

        assert isinstance(result.exception, RuntimeError)
        assert not project_config.is_set()
        assert not active_workspace.is_set()

    @pytest.mark.parametrize("filename", ["custom.toml", "vcsinstall.lock"])
    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_dependency_add_rejects_invalid_project_files(self, tmp_path: Path, filename: str, protocol: str) -> None:
        target = tmp_path / "custom.toml"
        target.write_text("version = 1\n")
        (tmp_path / filename).write_text("invalid[")

        result = CliRunner().invoke(
            application.app, ["-p", protocol, "--config", str(target), "dependency", "add", "ci", "unused-source"]
        )

        assert result.exit_code == 2
        assert not result.stderr
        assert str(tmp_path / filename) in result.stdout
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        if protocol == "automation":
            record = json.loads(result.stdout)
            assert record["type"] == "error"
            assert record["code"] == config_errors.InvalidToml.model_fields["code"].default
        elif protocol == "llm":
            assert "--VCSINSTALL-CELL " in result.stdout

    def test_dependency_add_requires_configuration(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.toml"

        result = CliRunner().invoke(
            application.app, ["-p", "automation", "--config", str(missing), "dependency", "add", "ci", "unused-source"]
        )

        assert result.exit_code == 2
        record = json.loads(result.stdout)
        assert record["path"] == str(missing)
        assert not missing.exists()

    @pytest.mark.parametrize("arguments", [[], ["ci"], ["ci", "repo", "extra"]])
    def test_dependency_add_requires_name_and_repository(self, arguments: list[str]) -> None:
        result = CliRunner().invoke(application.app, ["dependency", "add", *arguments])

        assert result.exit_code == 2

    def test_dependency_add_rejects_invalid_name_before_loading(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            application.app,
            ["-p", "automation", "--config", str(tmp_path / "missing"), "dependency", "add", "Bad/Name", "source"],
        )

        assert result.exit_code == 2
        assert "Bad/Name" in result.output
        assert not project_config.is_set()
        assert not active_workspace.is_set()

    def test_help_identifies_stub(self) -> None:
        result = CliRunner().invoke(application.app, ["dependency", "add", "--help"])

        assert result.exit_code == 0
        assert "not implemented yet" in result.stdout

    @pytest.mark.parametrize("filename", ["custom.toml", "vcsinstall.lock"])
    def test_adapter_diagnostics_reach_cli(self, tmp_path: Path, filename: str) -> None:
        config = tmp_path / "custom.toml"
        config.write_text("version = 1\n")
        text = 'version = 1\n[dependencies.ci]\nvcs = "git"\nrepository = "unused-source"\nbranch = "bad..branch"\n'
        if filename.endswith("lock"):
            text += 'revision = "' + "1" * 40 + '"\n'
        (tmp_path / filename).write_text(text)

        result = CliRunner().invoke(
            application.app, ["-p", "automation", "--config", str(config), "dependency", "add", "ci", "unused-source"]
        )

        assert result.exit_code == 2
        assert not result.stderr
        record = json.loads(result.stdout)
        assert record["code"] == workspace_errors.DependencyValidation.model_fields["code"].default
        assert record["path"] == str(tmp_path / filename)
        assert record["dependency"] == "ci"
        assert record["source_code"] == source_errors.InvalidGitReference.model_fields["code"].default
        assert record["source_field"] == "branch"
        assert record["source_value"] == "bad..branch"
