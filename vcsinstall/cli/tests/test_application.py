import json
from importlib import metadata
from pathlib import Path

import pytest
from filelock import FileLock, Timeout
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.output import StreamOutputEmitter, output
from llm_tool_cli.paths import PathInput
from llm_tool_cli.protocol.cell_shortcuts import operation_succeeded
from llm_tool_cli.protocol.logic_cells.base import LogicCell
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cache import active_cache_root
from vcsinstall.cache.tests.helpers import held_lock
from vcsinstall.cli import application
from vcsinstall.cli.application import main
from vcsinstall.cli.tests.helpers import coordinated_cli, wait_for_phase
from vcsinstall.workspace import Config, Lockfile, active_workspace, errors, project_operation


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
            ["dependency", "outdated", "--help"],
            ["dependency", "outdated", "-h"],
            ["dependency", "update", "--help"],
            ["dependency", "update", "-h"],
            ["cache"],
            ["cache", "--help"],
            ["cache", "-h"],
            ["cache", "clean", "--help"],
            ["cache", "clean", "-h"],
            ["add", "--help"],
            ["add", "-h"],
            ["remove", "--help"],
            ["remove", "-h"],
            ["install", "--help"],
            ["install", "-h"],
            ["list", "--help"],
            ["list", "-h"],
        ],
    )
    def test_project_independent_interfaces_ignore_unusable_config_and_cache(
        self, tmp_path: Path, arguments: list[str], project_state_root: Path, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "vcsinstall.workspace.coordination.user_state_path", side_effect=AssertionError("Unexpected state")
        )
        cache = tmp_path / "cache-loop"
        cache.symlink_to(cache)
        result = CliRunner().invoke(
            application.app,
            ["--config", str(tmp_path / "missing" / "config.toml"), "--cache-dir", str(cache), *arguments],
        )

        assert result.exit_code == (2 if arguments in (["dependency"], ["cache"]) else 0), result.output
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert list(tmp_path.iterdir()) == [cache]
        assert not project_state_root.exists()


@pytest.mark.usefixtures("isolated_settings")
class TestProjectCoordination:
    @pytest.fixture(autouse=True)
    def initialize_settings(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.mark.parametrize(
        "arguments",
        [
            ["init"],
            ["list"],
            ["install"],
            ["add", "ci@/file", "@/installed"],
            ["remove", "@/installed"],
            ["dependency", "add", "--vcs", "filesystem", "ci", "./source"],
            ["dependency", "list"],
            ["dependency", "remove", "ci"],
            ["dependency", "outdated"],
            ["dependency", "update"],
            ["dependency", "push", "ci", "--mode", "patch", "-o", "./contribution.patch"],
        ],
    )
    def test_project_commands_lock_before_loading_or_creating(
        self, tmp_path: Path, project_state_root: Path, mocker: MockerFixture, arguments: list[str]
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("invalid[")
        lockfile = tmp_path / "vcsinstall.lock"
        lockfile.write_text("invalid too[")
        installed = tmp_path / "installed"
        installed.write_bytes(b"local content")
        with project_operation(cwd=PathInput(tmp_path)) as selected:
            selected.unwrap()
        path = next(project_state_root.glob("projects/*/operation.lock"))
        cache = tmp_path / "separate-cache"
        mocker.patch("vcsinstall.workspace.coordination.LOCK_TIMEOUT_SECONDS", 0)

        with held_lock(path):
            result = CliRunner().invoke(
                application.app,
                ["-p", "automation", "--config", str(config), "--cache-dir", str(cache), *arguments],
            )

        assert result.exit_code == 3, result.output
        assert not result.stderr
        record = json.loads(result.stdout)
        assert record["code"] == errors.ProjectLockTimeout.model_fields["code"].default
        assert record["project_root"] == str(tmp_path)
        assert record["path"] == str(path)
        assert float(record["timeout"]) == 0
        assert config.read_text() == "invalid["
        assert lockfile.read_text() == "invalid too["
        assert installed.read_bytes() == b"local content"
        assert not cache.exists()
        assert not active_workspace.is_set()
        assert not project_config.is_set()

    @pytest.mark.parametrize("exists", [False, True])
    def test_init_holds_project_lock_through_output_delivery(
        self, tmp_path: Path, project_state_root: Path, mocker: MockerFixture, exists: bool
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        if exists:
            config.write_text("version = 1\n")

        class InspectingEmitter(StreamOutputEmitter):
            __slots__ = ()

            def _render(self, cells: list[LogicCell]) -> str:
                assert config.is_file()
                assert not active_workspace.is_set()
                if not exists:
                    assert project_config.get().config_path == config
                path = next(project_state_root.glob("projects/*/operation.lock"))
                with pytest.raises(Timeout), FileLock(path, timeout=0, preserve_lock_file=True):
                    pass
                return super()._render(cells)

        mocker.patch("llm_tool_cli.cli.context.StreamOutputEmitter", InspectingEmitter)
        result = CliRunner().invoke(application.app, ["-p", "automation", "--config", str(config), "init"])

        assert result.exit_code == (2 if exists else 0), result.exception
        assert json.loads(result.stdout)["type"] == ("error" if exists else "operation_succeeded")
        assert not result.stderr
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        path = next(project_state_root.glob("projects/*/operation.lock"))
        with FileLock(path, timeout=0, preserve_lock_file=True):
            pass

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_state_access_failure_uses_shared_error_protocol(
        self, tmp_path: Path, project_state_root: Path, protocol: str
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        project_state_root.write_text("unrelated file")
        result = CliRunner().invoke(application.app, ["-p", protocol, "--config", str(config), "list"])
        assert result.exit_code == 3, result.output
        assert not result.stderr
        assert str(tmp_path) in result.stdout
        if protocol == "automation":
            assert json.loads(result.stdout)["code"] == errors.ProjectLockAccessFailed.model_fields["code"].default
        elif protocol == "llm":
            assert "--VCSINSTALL-CELL " in result.stdout
        else:
            assert "Error:" in result.stdout

    def test_two_writers_preserve_changes_with_different_cache_directories(self, tmp_path: Path) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text(
            'version = 1\n[dependencies.first]\nvcs = "filesystem"\nrepository = "./source"\n'
            '[dependencies.second]\nvcs = "filesystem"\nrepository = "./source"\n'
        )
        lockfile = tmp_path / "vcsinstall.lock"
        lockfile.write_text(
            config.read_text().replace(
                'repository = "./source"\n', 'repository = "./source"\nrevision = "sha256:' + "0" * 64 + '"\n'
            )
        )
        common = ["-p", "automation", "--config", str(config)]
        with coordinated_cli(
            [*common, "--cache-dir", str(tmp_path / "cache-a"), "dependency", "remove", "first"],
            pause_after_loading=True,
        ) as first:
            wait_for_phase(first, b"acquiring")
            wait_for_phase(first, b"loaded")
            with coordinated_cli(
                [*common, "--cache-dir", str(tmp_path / "cache-b"), "dependency", "remove", "second"]
            ) as second:
                wait_for_phase(second, b"acquiring")
                output, diagnostic = first.communicate(input=b"continue", timeout=10)
                assert first.returncode == 0, (output, diagnostic)
                output, diagnostic = second.communicate(timeout=10)
                assert second.returncode == 0, (output, diagnostic)

        import tomllib

        assert not Config.model_validate(tomllib.loads(config.read_text())).dependencies
        assert not Lockfile.model_validate(tomllib.loads(lockfile.read_text())).dependencies
        assert not (tmp_path / "cache-a").exists()
        assert not (tmp_path / "cache-b").exists()

    def test_process_termination_releases_project_lock(self, tmp_path: Path, project_state_root: Path) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        arguments = ["-p", "automation", "--config", str(config), "list"]
        with coordinated_cli(arguments, pause_after_loading=True) as process:
            wait_for_phase(process, b"acquiring")
            wait_for_phase(process, b"loaded")
            path = next(project_state_root.glob("projects/*/operation.lock"))
            identity = path.stat().st_ino
            process.kill()
            process.communicate(timeout=5)

        result = CliRunner().invoke(application.app, arguments)
        assert result.exit_code == 0, result.output
        assert path.stat().st_ino == identity
        with FileLock(path, timeout=0, preserve_lock_file=True):
            pass

    def test_internal_failure_releases_project_lock(
        self, tmp_path: Path, project_state_root: Path, mocker: MockerFixture
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        failure = RuntimeError("operation failed")
        mocker.patch("vcsinstall.cli.commands.entries.entry_list", side_effect=failure)
        result = CliRunner().invoke(application.app, ["--config", str(config), "list"])
        assert result.exception is failure
        assert not active_workspace.is_set()
        assert not project_config.is_set()
        path = next(project_state_root.glob("projects/*/operation.lock"))
        with FileLock(path, timeout=0, preserve_lock_file=True):
            pass

    @pytest.mark.parametrize("fail", [False, True])
    def test_project_resources_remain_active_through_output_delivery(
        self, tmp_path: Path, project_state_root: Path, mocker: MockerFixture, fail: bool
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        cache = tmp_path / "cache"

        class InspectingEmitter(StreamOutputEmitter):
            __slots__ = ()

            def _render(self, cells: list[LogicCell]) -> str:
                assert active_workspace.get().config_path == config
                assert project_config.get().config_path == config
                assert active_cache_root.get() == cache
                path = next(project_state_root.glob("projects/*/operation.lock"))
                with pytest.raises(Timeout), FileLock(path, timeout=0, preserve_lock_file=True):
                    pass
                return super()._render(cells)

        def operation(*, locked: bool) -> Result[None]:
            output.get().emit([operation_succeeded("Completed")]).unwrap()
            return Err([InvalidArguments(reason="Operation failed")]) if fail else Ok(None)

        mocker.patch("llm_tool_cli.cli.context.StreamOutputEmitter", InspectingEmitter)
        mocker.patch("vcsinstall.cli.commands.entries.install", side_effect=operation)
        result = CliRunner().invoke(
            application.app, ["-p", "automation", "--config", str(config), "--cache-dir", str(cache), "install"]
        )

        assert result.exit_code == (1 if fail else 0), result.exception
        assert [json.loads(line)["type"] for line in result.stdout.splitlines()] == [
            "error" if fail else "operation_succeeded"
        ]
        assert not active_workspace.is_set()
        assert not project_config.is_set()
        assert not active_cache_root.is_set()
        assert not cache.exists()
        path = next(project_state_root.glob("projects/*/operation.lock"))
        with FileLock(path, timeout=0, preserve_lock_file=True):
            pass

    def test_cache_cleanup_preserves_project_coordination(self, tmp_path: Path, project_state_root: Path) -> None:
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
            selected.unwrap()
            path = next(project_state_root.glob("projects/*/operation.lock"))
            identity = path.stat().st_ino
            result = CliRunner().invoke(application.app, ["--cache-dir", str(project_state_root), "cache", "clean"])
            assert result.exit_code == 0, result.output
            assert path.stat().st_ino == identity

        assert path.is_file()
