from pathlib import Path

import pytest
from filelock import FileLock, Timeout
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.core import settings
from llm_tool_cli.core.entities import ToolName
from llm_tool_cli.file_locks import errors as lock_errors
from llm_tool_cli.file_locks.tests.helpers import held_lock
from llm_tool_cli.paths import PathInput, ProjectConfigPath
from llm_tool_cli.paths import errors as path_errors
from llm_tool_cli.tool_state import errors as state_errors
from pytest_mock import MockerFixture

from vcsinstall.workspace import project_operation


@pytest.mark.usefixtures("isolated_settings")
class TestProjectOperation:
    @pytest.fixture(autouse=True)
    def initialize_settings(self, isolated_settings: None) -> None:
        settings.initialize(name=ToolName("vcsinstall"), config_filename=settings.ConfigFilename("vcsinstall.toml"))

    def test_selects_without_loading_and_keeps_coordination_file(
        self, tmp_path: Path, project_state_root: Path
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("invalid[")
        lockfile = tmp_path / "vcsinstall.lock"
        lockfile.write_text("also invalid[")
        with project_operation(cwd=PathInput(tmp_path)) as selected:
            assert selected.unwrap() == config
            path = next(project_state_root.glob("projects/*/operation.lock"))
            external = FileLock(path, timeout=0, preserve_lock_file=True)
            with pytest.raises(Timeout):
                external.acquire()

        with external:
            pass
        assert path.is_file()
        assert config.read_text() == "invalid["
        assert lockfile.read_text() == "also invalid["

    def test_root_alias_and_config_filenames_share_lock(self, tmp_path: Path, project_state_root: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        (root / "vcsinstall.toml").write_text("version = 1\n")
        with project_operation(cwd=PathInput(root)) as selected:
            selected.unwrap()
        path = next(project_state_root.glob("projects/*/operation.lock"))

        with held_lock(path):
            with project_operation(cwd=PathInput(alias), path=ProjectConfigPath(Path("other.toml"))) as blocked:
                problem = blocked.unwrap_err()[0]
                assert isinstance(problem, lock_errors.Timeout)
                assert problem.path == path
                assert problem.timeout == 1
                assert isinstance(problem.cause, Timeout)

        assert list(project_state_root.glob("projects/*/operation.lock")) == [path]
        assert not (root / "other.toml").exists()

    def test_different_projects_do_not_block_each_other(self, tmp_path: Path, project_state_root: Path) -> None:
        first = tmp_path / "first.toml"
        with project_operation(cwd=PathInput(tmp_path), path=ProjectConfigPath(first)) as selected:
            selected.unwrap()
        path = next(project_state_root.glob("projects/*/operation.lock"))

        with held_lock(path):
            with project_operation(
                cwd=PathInput(tmp_path), path=ProjectConfigPath(tmp_path / "other" / "config.toml")
            ) as selected:
                assert selected.is_ok()

        assert len(list(project_state_root.glob("projects/*/operation.lock"))) == 2
        assert not (tmp_path / "other").exists()

    def test_nested_scope_keeps_lock_until_outer_exit(self, tmp_path: Path, project_state_root: Path) -> None:
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as outer:
            outer.unwrap()
            path = next(project_state_root.glob("projects/*/operation.lock"))
            external = FileLock(path, timeout=0, preserve_lock_file=True)
            with project_operation(cwd=PathInput(tmp_path), initialize=True) as inner:
                assert inner.unwrap() == outer.unwrap()
            with pytest.raises(Timeout):
                external.acquire()

        with external:
            pass

    def test_caller_exception_releases_lock(self, tmp_path: Path, project_state_root: Path) -> None:
        with pytest.raises(RuntimeError, match="consumer failed"):
            with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
                selected.unwrap()
                raise RuntimeError("consumer failed")

        path = next(project_state_root.glob("projects/*/operation.lock"))
        with FileLock(path, timeout=0, preserve_lock_file=True):
            pass

    def test_init_selects_current_directory_without_discovery(self, tmp_path: Path) -> None:
        (tmp_path / "vcsinstall.toml").write_text("version = 1\n")
        child = tmp_path / "child"
        child.mkdir()
        with project_operation(cwd=PathInput(child), initialize=True) as selected:
            assert selected.unwrap() == child / "vcsinstall.toml"
        assert not (child / "vcsinstall.toml").exists()

    def test_missing_configuration_preserves_discovery_error(self, tmp_path: Path, project_state_root: Path) -> None:
        with project_operation(cwd=PathInput(tmp_path)) as selected:
            assert isinstance(selected.unwrap_err()[0], config_errors.NotFound)
        assert not project_state_root.exists()

    @pytest.mark.parametrize("failure", [OSError("unavailable"), RuntimeError("unavailable")])
    def test_state_resolution_failure_has_no_fallback(
        self, tmp_path: Path, project_state_root: Path, mocker: MockerFixture, failure: Exception
    ) -> None:
        mocker.patch("llm_tool_cli.tool_state.directory.user_state_path", side_effect=failure)
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
            problem = selected.unwrap_err()[0]
            assert isinstance(problem, state_errors.StateDirectoryUnavailable)
            assert problem.tool_name == "vcsinstall"
            assert problem.path is None
            assert problem.cause is failure
        assert not project_state_root.exists()

    def test_relative_state_location_is_rejected(self, tmp_path: Path, mocker: MockerFixture) -> None:
        mocker.patch("llm_tool_cli.tool_state.directory.user_state_path", return_value=Path("relative-state"))
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
            problem = selected.unwrap_err()[0]
            assert isinstance(problem, state_errors.StateDirectoryUnavailable)
            assert problem.tool_name == "vcsinstall"
            assert problem.path == Path("relative-state")

    def test_unusable_state_directory_is_preserved(self, tmp_path: Path, project_state_root: Path) -> None:
        project_state_root.write_text("unrelated file")
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
            problem = selected.unwrap_err()[0]
            assert isinstance(problem, lock_errors.AccessFailed)
            assert isinstance(problem.cause, OSError)
        assert project_state_root.read_text() == "unrelated file"
        assert not (tmp_path / "vcsinstall.toml").exists()

    def test_coordination_directory_symlink_is_rejected(self, tmp_path: Path, project_state_root: Path) -> None:
        project_state_root.mkdir()
        (project_state_root / "projects").symlink_to(tmp_path, target_is_directory=True)
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
            assert isinstance(selected.unwrap_err()[0], path_errors.InvalidProjectPath)
        assert not list(tmp_path.iterdir())

    def test_nonregular_coordination_file_is_preserved(self, tmp_path: Path, project_state_root: Path) -> None:
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
            selected.unwrap()
        path = next(project_state_root.glob("projects/*/operation.lock"))
        path.unlink()
        path.mkdir()
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
            problem = selected.unwrap_err()[0]
            assert isinstance(problem, lock_errors.AccessFailed)
            assert problem.path == path
        assert path.is_dir()

    def test_native_lock_access_failure_is_returned(self, tmp_path: Path, mocker: MockerFixture) -> None:
        failure = PermissionError("access denied")
        mocker.patch("llm_tool_cli.file_locks.files.FileLock", side_effect=failure)
        with project_operation(cwd=PathInput(tmp_path), initialize=True) as selected:
            problem = selected.unwrap_err()[0]
            assert isinstance(problem, lock_errors.AccessFailed)
            assert problem.cause is failure
            assert problem.path is not None

    def test_unexpected_lock_exception_remains_internal(self, tmp_path: Path, mocker: MockerFixture) -> None:
        failure = RuntimeError("unexpected lock failure")
        mocker.patch("llm_tool_cli.file_locks.files.FileLock", side_effect=failure)
        with pytest.raises(RuntimeError) as caught:
            with project_operation(cwd=PathInput(tmp_path), initialize=True):
                pytest.fail("Unexpected lock failures must not be returned as environment errors")
        assert caught.value is failure

    def test_unconfigured_application_remains_an_internal_error(self, tmp_path: Path) -> None:
        with settings.static_config.scope(settings.StaticConfig(tool_name=ToolName("vcsinstall"))):
            with pytest.raises(config_errors.ConfigFilenameNotSet):
                with project_operation(cwd=PathInput(tmp_path)):
                    pytest.fail("An unconfigured application must not start an operation")
