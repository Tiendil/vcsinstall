import json
from importlib import metadata
from pathlib import Path

import pytest
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core import settings
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cli import application
from vcsinstall.cli.application import main
from vcsinstall.workspace import Config, active_workspace


@pytest.mark.usefixtures("isolated_settings")
class TestMain:
    def test_help(self, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]) -> None:
        mocker.patch("sys.argv", ["vcsinstall", "--help"])

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert "Install files into your repository" in captured.out

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_version_and_skill(self, mocker: MockerFixture, capsys: pytest.CaptureFixture[str], protocol: str) -> None:
        mocker.patch("sys.argv", ["vcsinstall", "-p", protocol, "version"])

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert metadata.version("vcsinstall") in captured.out

        mocker.patch("sys.argv", ["vcsinstall", "-p", protocol, "skill", "usage"])

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert "# `vcsinstall` Usage" in captured.out

    def test_invalid_protocol(self, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]) -> None:
        mocker.patch("sys.argv", ["vcsinstall", "-p", "invalid", "version"])

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code != 0
        captured = capsys.readouterr()
        assert not captured.err
        assert "--VCSINSTALL-CELL " in captured.out


@pytest.mark.usefixtures("isolated_settings")
class TestApplication:
    @pytest.fixture(autouse=True)
    def initialize_settings(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_init_creates_valid_commented_starter_without_lock(self, tmp_path: Path, protocol: str) -> None:
        target = tmp_path / "custom.toml"

        result = CliRunner().invoke(application.app, ["-p", protocol, "--config", str(target), "init"])

        assert result.exit_code == 0, result.output
        assert not result.stderr
        text = target.read_text()
        assert "# [dependencies.shared]" in text
        assert "# [[entries]]" in text
        assert not (tmp_path / "vcsinstall.lock").exists()
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        import tomllib

        config = Config.model_validate(tomllib.loads(text))
        assert not config.dependencies
        assert not config.entries
        if protocol == "automation":
            records = [json.loads(line) for line in result.stdout.splitlines()]
            assert len(records) == 1
            assert str(target) in result.stdout
        elif protocol == "llm":
            assert "--VCSINSTALL-CELL " in result.stdout

    def test_init_uses_current_directory_instead_of_discovery(self, tmp_path: Path, mocker: MockerFixture) -> None:
        (tmp_path / "vcsinstall.toml").write_text("invalid[")
        child = tmp_path / "child"
        child.mkdir()
        mocker.patch("pathlib.Path.cwd", return_value=child)

        result = CliRunner().invoke(application.app, ["init"])

        assert result.exit_code == 0, result.output
        assert (child / "vcsinstall.toml").exists()
        assert (tmp_path / "vcsinstall.toml").read_text() == "invalid["

    def test_init_never_overwrites_and_ignores_existing_lock(self, tmp_path: Path) -> None:
        target = tmp_path / "vcsinstall.toml"
        (tmp_path / "vcsinstall.lock").write_text("malformed[")
        args = ["-p", "automation", "--config", str(target), "init"]

        first = CliRunner().invoke(application.app, args)
        original = target.read_bytes()
        second = CliRunner().invoke(application.app, args)

        assert first.exit_code == 0
        assert second.exit_code == 2
        assert json.loads(second.stdout)["code"] == config_errors.AlreadyExists.model_fields["code"].default
        assert target.read_bytes() == original
        assert (tmp_path / "vcsinstall.lock").read_text() == "malformed["

    def test_init_missing_parent_is_not_created(self, tmp_path: Path) -> None:
        target = tmp_path / "missing" / "vcsinstall.toml"

        result = CliRunner().invoke(application.app, ["-p", "automation", "--config", str(target), "init"])

        assert result.exit_code == 2
        assert json.loads(result.stdout)["code"] == config_errors.Unwritable.model_fields["code"].default
        assert not target.parent.exists()

    @pytest.mark.parametrize(
        "arguments",
        [
            ["--help"],
            ["-h"],
            ["version"],
            ["skill", "usage"],
            ["init", "--help"],
            ["dependency"],
            ["dependency", "--help"],
            ["dependency", "add", "--help"],
            ["dependency", "add", "-h"],
        ],
    )
    def test_project_independent_interfaces_ignore_unusable_config(self, tmp_path: Path, arguments: list[str]) -> None:
        result = CliRunner().invoke(
            application.app, ["--config", str(tmp_path / "missing" / "config.toml"), *arguments]
        )

        assert result.exit_code == (2 if arguments == ["dependency"] else 0), result.output
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert not list(tmp_path.iterdir())
