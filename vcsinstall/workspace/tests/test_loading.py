from pathlib import Path

import pytest
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config.project import ProjectConfig, project_config
from llm_tool_cli.core import errors as core_errors
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Result
from llm_tool_cli.paths import PathInput, ProjectConfigPath
from llm_tool_cli.paths import errors as path_errors

from vcsinstall.domain import Dependency, DependencyName, VcsId
from vcsinstall.sources import DEFAULT_ADAPTERS
from vcsinstall.sources import errors as source_errors
from vcsinstall.sources.tests.helpers import RecordingAdapter
from vcsinstall.workspace import active_workspace
from vcsinstall.workspace import errors as workspace_errors
from vcsinstall.workspace import load_workspace

_DEPENDENCY = '\n[dependencies.ci]\nvcs = "git"\nrepository = "../source"\nbranch = "main"\n'
_LOCK = "version = 1\n" + _DEPENDENCY + 'revision = "' + "1" * 40 + '"\n'


@pytest.mark.usefixtures("isolated_settings")
class TestLoadWorkspace:
    @pytest.fixture(autouse=True)
    def initialize_settings(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    def test_discovers_nearest_project_without_activation(self, tmp_path: Path) -> None:
        (tmp_path / "vcsinstall.toml").write_text("version = 1\n")
        project = tmp_path / "project"
        project.mkdir()
        (project / "vcsinstall.toml").write_text("version = 1\n" + _DEPENDENCY)
        nested = project / "nested"
        nested.mkdir()
        outer = ProjectConfig(config_path=ProjectConfigPath(tmp_path / "other.toml"))

        with project_config.scope(outer):
            workspace = load_workspace(cwd=PathInput(nested)).unwrap()
            assert workspace.project_root == project
            assert workspace.config_path == project / "vcsinstall.toml"
            assert workspace.lockfile_path == project / "vcsinstall.lock"
            assert workspace.lockfile is None
            assert project_config.get() is outer
            assert not active_workspace.is_set()
        assert not project_config.is_set()
        assert not (project / "vcsinstall.lock").exists()

    def test_explicit_config_selects_sibling_lock_independently_of_cwd(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        config_path = project / "custom.toml"
        text = (
            "version = 1\n"
            + _DEPENDENCY
            + ('\n[[entries]]\ndependency = "ci"\nsource_path = "@/a/../LICENSE"\ndestination = "@/docs/LICENSE"\n')
        )
        config_path.write_text(text)
        (project / "vcsinstall.lock").write_text(_LOCK)
        (tmp_path / "vcsinstall.lock").write_text("invalid toml[")

        result = load_workspace(cwd=PathInput(tmp_path), path=ProjectConfigPath(Path("project/custom.toml")))
        workspace = result.unwrap()
        assert workspace.config.entries[0].destination == "@/docs/LICENSE"
        assert workspace.config.entries[0].source_path == "@/LICENSE"
        assert workspace.config.dependencies[DependencyName("ci")].repository == "../source"
        assert workspace.lockfile is not None
        assert workspace.lockfile.dependencies[DependencyName("ci")].revision == "1" * 40
        assert config_path.read_text() == text
        assert not (project / "docs").exists()

    def test_valid_stale_lock_is_retained(self, tmp_path: Path) -> None:
        (tmp_path / "vcsinstall.toml").write_text("version = 1\n")
        lock_text = _LOCK + (
            '\n[[entries]]\ndependency = "ci"\nsource_path = "@/old"\n' 'destination = "@/old"\nkind = "absent"\n'
        )
        (tmp_path / "vcsinstall.lock").write_text(lock_text)

        result = load_workspace(cwd=PathInput(tmp_path))
        workspace = result.unwrap()
        assert not workspace.config.dependencies
        assert workspace.lockfile is not None
        assert len(workspace.lockfile.entries) == 1
        assert (tmp_path / "vcsinstall.lock").read_text() == lock_text

    def test_no_explicit_path_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "vcsinstall.toml").write_text("version = 1\n")
        missing = tmp_path / "missing.toml"

        result = load_workspace(cwd=PathInput(tmp_path), path=ProjectConfigPath(missing))
        error = result.unwrap_err()[0]
        assert isinstance(error, config_errors.Unreadable)
        assert error.path == missing
        assert isinstance(error.cause, FileNotFoundError)
        assert not project_config.is_set()
        assert not active_workspace.is_set()

    @pytest.mark.parametrize(
        "data,error_type",
        [
            (b"", config_errors.ValidationFailed),
            (b"version = 2", config_errors.ValidationFailed),
            (b"version = true", config_errors.ValidationFailed),
            (b"version = 1\nunknown = 1", config_errors.ValidationFailed),
            (b"invalid[", config_errors.InvalidToml),
            (b"\xff", config_errors.InvalidEncoding),
        ],
    )
    @pytest.mark.parametrize("filename", ["vcsinstall.toml", "vcsinstall.lock"])
    def test_rejects_invalid_files_with_shared_diagnostics(
        self, tmp_path: Path, data: bytes, error_type: type[config_errors.EnvironmentError], filename: str
    ) -> None:
        (tmp_path / "vcsinstall.toml").write_text("version = 1\n")
        (tmp_path / filename).write_bytes(data)

        result = load_workspace(cwd=PathInput(tmp_path))
        error = result.unwrap_err()[0]
        assert isinstance(error, error_type)
        assert error.path == tmp_path / filename
        assert error.reason
        assert error.cause is not None
        assert not project_config.is_set()
        assert (tmp_path / filename).read_bytes() == data

    @pytest.mark.parametrize("filename", ["vcsinstall.toml", "vcsinstall.lock"])
    @pytest.mark.parametrize("target", ["internal", "outside", "missing", "loop"])
    def test_rejects_project_file_symlinks(self, tmp_path: Path, filename: str, target: str) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "vcsinstall.toml").write_text("version = 1\n")
        link = project / filename
        if link.exists():
            link.unlink()
        targets = {
            "internal": project / "real",
            "outside": tmp_path / "real",
            "missing": project / "missing",
            "loop": link,
        }
        if target in {"internal", "outside"}:
            targets[target].write_text("version = 1\n")
        link.symlink_to(targets[target])

        result = load_workspace(cwd=PathInput(project))
        error = result.unwrap_err()[0]
        assert isinstance(error, path_errors.InvalidProjectPath)
        assert error.path == str(link)
        assert link.is_symlink()

    @pytest.mark.parametrize("kind", ["directory", "fifo"])
    def test_rejects_nonregular_lockfile(self, tmp_path: Path, kind: str) -> None:
        import os

        (tmp_path / "vcsinstall.toml").write_text("version = 1\n")
        lockfile = tmp_path / "vcsinstall.lock"
        if kind == "directory":
            lockfile.mkdir()
        else:
            os.mkfifo(lockfile)

        result = load_workspace(cwd=PathInput(tmp_path))
        error = result.unwrap_err()[0]
        assert isinstance(error, config_errors.Unreadable)
        assert error.path == lockfile

    def test_missing_lock_is_not_discovered_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / "vcsinstall.lock").write_text("invalid[")
        project = tmp_path / "child"
        project.mkdir()
        (project / "vcsinstall.toml").write_text("version = 1\n")

        result = load_workspace(cwd=PathInput(project))
        assert result.unwrap().lockfile is None

    @pytest.mark.parametrize(
        "filename,text,problem_type",
        [
            (
                "vcsinstall.toml",
                "version = 1\n" + _DEPENDENCY.replace('branch = "main"', 'branch = "bad..branch"'),
                source_errors.InvalidGitReference,
            ),
            (
                "vcsinstall.toml",
                "version = 1\n" + _DEPENDENCY.replace('vcs = "git"', 'vcs = "filesystem"'),
                source_errors.UnsupportedSelector,
            ),
            (
                "vcsinstall.toml",
                "version = 1\n" + _DEPENDENCY.replace('branch = "main"', 'rev = "HEAD"'),
                source_errors.InvalidRevision,
            ),
            ("vcsinstall.lock", _LOCK.replace('branch = "main"\n', ""), source_errors.MissingGitSelector),
            (
                "vcsinstall.lock",
                _LOCK.replace('branch = "main"', 'rev = "' + "2" * 40 + '"'),
                source_errors.RevisionMismatch,
            ),
            (
                "vcsinstall.lock",
                _LOCK.replace('revision = "' + "1" * 40 + '"', 'revision = "HEAD"'),
                source_errors.InvalidRevision,
            ),
        ],
    )
    def test_source_errors_retain_file_and_dependency_context(
        self, tmp_path: Path, filename: str, text: str, problem_type: type[source_errors.EnvironmentError]
    ) -> None:
        (tmp_path / "vcsinstall.toml").write_text("version = 1\n")
        (tmp_path / filename).write_text(text)

        result = load_workspace(cwd=PathInput(tmp_path))
        error = result.unwrap_err()[0]
        assert isinstance(error, workspace_errors.DependencyValidation)
        assert error.path == tmp_path / filename
        assert error.dependency == "ci"
        assert isinstance(error.problem, problem_type)
        assert str(error.path) in error.format_message()
        assert "dependencies.ci" in error.format_message()
        assert error.problem.format_message() in error.format_message()
        record = error.as_record()
        for key, value in error.problem.as_record().items():
            if key not in {"type", "message"}:
                assert record["source_" + key] == value
        assert "problem" not in record

    def test_explicit_registry_is_scoped_and_validates_both_files(self, tmp_path: Path) -> None:
        config = tmp_path / "vcsinstall.toml"
        lock = tmp_path / "vcsinstall.lock"
        config_text = "version = 1\n" + _DEPENDENCY.replace('vcs = "git"', 'vcs = "noop"')
        lock_text = _LOCK.replace('vcs = "git"', 'vcs = "noop"').replace("1" * 40, "opaque-test-revision")
        config.write_text(config_text)
        lock.write_text(lock_text)
        adapter = RecordingAdapter()

        assert load_workspace(cwd=PathInput(tmp_path)).is_ok()
        result = load_workspace(cwd=PathInput(tmp_path), adapters={**DEFAULT_ADAPTERS, VcsId.noop: adapter})
        workspace = result.unwrap()
        assert adapter.dependencies == [workspace.config.dependencies[DependencyName("ci")]]
        assert workspace.lockfile is not None
        assert adapter.locks == [(workspace.lockfile.dependencies[DependencyName("ci")], "opaque-test-revision")]
        assert load_workspace(cwd=PathInput(tmp_path)).is_ok()
        assert len(adapter.dependencies) == len(adapter.locks) == 1
        assert not project_config.is_set()
        assert config.read_text() == config_text
        assert lock.read_text() == lock_text

    def test_injected_adapter_replaces_builtin_policy(self, tmp_path: Path) -> None:
        (tmp_path / "vcsinstall.toml").write_text("version = 1\n" + _DEPENDENCY.replace("main", "bad..branch"))
        adapter = RecordingAdapter()

        result = load_workspace(cwd=PathInput(tmp_path), adapters={**DEFAULT_ADAPTERS, VcsId.git: adapter})
        assert result.is_ok()
        assert len(adapter.dependencies) == 1
        assert adapter.dependencies[0].branch == "bad..branch"
        result = load_workspace(cwd=PathInput(tmp_path))
        assert result.is_err()

    def test_adapter_error_retains_shared_data_and_private_cause(self, tmp_path: Path) -> None:
        (tmp_path / "vcsinstall.toml").write_text("version = 1\n" + _DEPENDENCY)
        cause = PermissionError("denied")
        problem = config_errors.Unreadable(path=tmp_path / "source", reason="denied").with_cause(cause)
        adapter = RecordingAdapter([problem])

        result = load_workspace(cwd=PathInput(tmp_path), adapters={**DEFAULT_ADAPTERS, VcsId.git: adapter})
        error = result.unwrap_err()[0]
        assert isinstance(error, workspace_errors.DependencyValidation)
        assert error.problem is problem
        assert error.problem.cause is cause
        record = error.as_record()
        assert record["source_code"] == problem.code
        assert record["source_path"] == str(problem.path)
        assert record["source_reason"] == problem.reason
        assert "cause" not in record

    def test_unexpected_adapter_exception_remains_internal(self, tmp_path: Path) -> None:
        class BrokenAdapter(RecordingAdapter):
            def validate_dependency(self, dependency: Dependency) -> Result[None]:
                raise core_errors.InternalError("broken adapter")

        (tmp_path / "vcsinstall.toml").write_text("version = 1\n" + _DEPENDENCY)

        with pytest.raises(core_errors.InternalError):
            load_workspace(cwd=PathInput(tmp_path), adapters={**DEFAULT_ADAPTERS, VcsId.git: BrokenAdapter()})
        assert not project_config.is_set()

    @pytest.mark.parametrize("failure", ["config", "lockfile", "source"])
    def test_failed_load_preserves_active_workspace(self, tmp_path: Path, failure: str) -> None:
        outer = tmp_path / "outer"
        inner = tmp_path / "inner"
        for root in (outer, inner):
            root.mkdir()
            (root / "vcsinstall.toml").write_text("version = 1\n")
        if failure == "config":
            (inner / "vcsinstall.toml").write_text("invalid[")
        elif failure == "lockfile":
            (inner / "vcsinstall.lock").write_text("invalid[")
        else:
            (inner / "vcsinstall.toml").write_text("version = 1\n" + _DEPENDENCY.replace("main", "bad..branch"))

        workspace = load_workspace(cwd=PathInput(outer)).unwrap()
        with workspace.scope():
            result = load_workspace(cwd=PathInput(inner))
            assert result.is_err()
            assert active_workspace.get() is workspace
            assert project_config.get().project_dir == outer
        assert not active_workspace.is_set()
        assert not project_config.is_set()


@pytest.mark.usefixtures("isolated_settings")
class TestWorkspace:
    @pytest.fixture(autouse=True)
    def initialize_settings(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    def test_scope__activates_loaded_workspace_without_reloading(self, tmp_path: Path) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        config.write_text("invalid[")
        assert not active_workspace.is_set()
        assert not project_config.is_set()

        with workspace.scope():
            assert active_workspace.get() is workspace
            assert project_config.get().config_path == workspace.config_path
        assert not active_workspace.is_set()
        assert not project_config.is_set()
        assert config.read_text() == "invalid["

    def test_scope__restores_previous_workspace(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = tmp_path / "inner"
        for root in (outer, inner):
            root.mkdir()
            (root / "vcsinstall.toml").write_text("version = 1\n")
        outer_workspace = load_workspace(cwd=PathInput(outer)).unwrap()

        with outer_workspace.scope():
            inner_workspace = load_workspace(cwd=PathInput(inner)).unwrap()
            assert active_workspace.get() is outer_workspace
            assert project_config.get().project_dir == outer
            with inner_workspace.scope():
                assert active_workspace.get() is inner_workspace
                assert project_config.get().project_dir == inner
            assert active_workspace.get() is outer_workspace
            assert project_config.get().project_dir == outer
        assert not active_workspace.is_set()
        assert not project_config.is_set()

    def test_scope__restores_bindings_after_exception(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = tmp_path / "inner"
        for root in (outer, inner):
            root.mkdir()
            (root / "vcsinstall.toml").write_text("version = 1\n")
        outer_workspace = load_workspace(cwd=PathInput(outer)).unwrap()
        inner_workspace = load_workspace(cwd=PathInput(inner)).unwrap()

        with outer_workspace.scope():
            with pytest.raises(RuntimeError, match="operation failed"):
                with inner_workspace.scope():
                    assert active_workspace.get() is inner_workspace
                    raise RuntimeError("operation failed")
            assert active_workspace.get() is outer_workspace
            assert project_config.get().project_dir == outer
        assert not active_workspace.is_set()
        assert not project_config.is_set()
