from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import PathInput
from llm_tool_cli.paths import errors as path_errors
from pytest_mock import MockerFixture

from vcsinstall.cache import CacheRootPath
from vcsinstall.cache import errors as cache_errors
from vcsinstall.domain import Dependency, DependencyName, RepositoryLocator, SourceRevision, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.hosting import GitHostingAdapter
from vcsinstall.runtime import dependency_add
from vcsinstall.runtime.tests.conftest import CollectingEmitter
from vcsinstall.sources import ResolvedSource, get_adapter
from vcsinstall.sources.adapters.noop import NoopSourceAdapter
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import errors as workspace_errors
from vcsinstall.workspace import load_workspace


class ControlledHosting:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[Dependency] = []

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        raise AssertionError("Runtime must not probe repository input.")

    @contextmanager
    def acquire(self, dependency: Dependency) -> Iterator[Result[Path]]:
        self.requests.append(dependency)
        yield Ok(self.path)


class ControlledSource(NoopSourceAdapter):
    def resolve(self, dependency: Dependency, path: Path) -> Result[ResolvedSource]:
        return Ok(ResolvedSource(dependency=dependency, path=path, revision=SourceRevision("controlled revision")))


@pytest.mark.usefixtures("isolated_settings")
class TestDependencyAdd:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.mark.parametrize("vcs", [VcsId.git, VcsId.noop])
    def test_source_and_hosting_use_prepared_dependency(
        self, tmp_path: Path, emitter: CollectingEmitter, vcs: VcsId, mocker: MockerFixture
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        source = tmp_path / "source"
        revision = "controlled revision"
        if vcs == VcsId.git:
            client = create_repository(source)
            client.set_symbolic_reference("refs/remotes/origin/HEAD", "refs/heads/trunk").unwrap()
            revision = client.resolve_commit("HEAD").unwrap()
        locator = RepositoryLocator(str(source) if vcs == VcsId.git else "Literal Fixture Identity")
        prepared = Dependency(vcs=vcs, repository=locator)
        hosting = ControlledHosting(source)
        adapter = get_adapter(vcs) if vcs == VcsId.git else ControlledSource()
        probe = mocker.spy(adapter, "probe_repository")
        normalize = mocker.spy(adapter, "normalize_repository")
        cache = tmp_path / "cache"
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()

        with workspace.scope():
            dependency_add(DependencyName("ci"), prepared, source=adapter, hosting=hosting).unwrap()

        probe.assert_not_called()
        normalize.assert_not_called()
        assert hosting.requests == [prepared]
        assert len(emitter.cells) == 1
        record = emitter.cells[0].render_automation()[0]
        assert record.meta["revision"] == revision
        assert record.meta["repository"] == locator
        assert not cache.exists()

    def test_cache_lock_error_prevents_acquisition(self, tmp_path: Path, emitter: CollectingEmitter) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        cache = tmp_path / "cache"
        (cache / "cache.lock").mkdir(parents=True)
        prepared = Dependency(vcs=VcsId.git, repository=RepositoryLocator(str(tmp_path / "missing-source")))
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()

        with workspace.scope():
            result = dependency_add(
                DependencyName("ci"),
                prepared,
                source=get_adapter(prepared.vcs),
                hosting=GitHostingAdapter(root=CacheRootPath(cache)),
            )

        failure = result.unwrap_err()[0]
        assert isinstance(failure, cache_errors.AccessFailed)
        assert failure.path == cache
        assert config.read_text() == "version = 1\n"
        assert not (tmp_path / "vcsinstall.lock").exists()
        assert not emitter.cells
        assert not (cache / "repos").exists()

    def test_input_validation_precedes_acquisition(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        prepared = make.dependency(vcs="noop", selector=make.selector("unresolved", "fixture reference"))
        hosting = ControlledHosting(tmp_path / "source")
        source = ControlledSource()
        problem = InvalidArguments(reason="Unsupported fixture selection.")
        validate = mocker.patch.object(source, "validate_input", return_value=Err([problem]))
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()

        with workspace.scope():
            result = dependency_add(DependencyName("ci"), prepared, source=source, hosting=hosting)

        assert result.is_err(InvalidArguments)
        assert result.unwrap_err()[0] is problem
        validate.assert_called_once_with(prepared)
        assert not hosting.requests
        assert not emitter.cells
        assert config.read_text() == "version = 1\n"
        assert not (tmp_path / "vcsinstall.lock").exists()

    def test_duplicate_name_fails_before_acquisition(self, tmp_path: Path, emitter: CollectingEmitter) -> None:
        config = tmp_path / "vcsinstall.toml"
        original = 'version = 1\n[dependencies.ci]\nvcs = "filesystem"\nrepository = "source"\n'
        config.write_text(original)
        prepared = Dependency(vcs=VcsId.filesystem, repository=RepositoryLocator(str(tmp_path / "source")))
        hosting = ControlledHosting(tmp_path / "source")
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()

        with workspace.scope():
            result = dependency_add(DependencyName("ci"), prepared, source=get_adapter(prepared.vcs), hosting=hosting)

        assert result.is_err(InvalidArguments)
        assert not hosting.requests
        assert not emitter.cells
        assert config.read_text() == original

    def test_orphaned_baseline_fails_before_acquisition(self, tmp_path: Path, emitter: CollectingEmitter) -> None:
        config = tmp_path / "vcsinstall.toml"
        configuration = 'version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "source"\n'
        config.write_text(configuration)
        lockfile = tmp_path / "vcsinstall.lock"
        recorded = configuration + (
            'revision = "recorded-revision"\n'
            '[[entries]]\ndependency = "ci"\nsource_path = "@/guide"\n'
            'destination = "@/installed"\nkind = "absent"\n'
        )
        lockfile.write_text(recorded)
        hosting = ControlledHosting(tmp_path / "source")
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()

        with workspace.scope():
            result = dependency_add(
                DependencyName("ci"),
                make.dependency(vcs="noop"),
                source=ControlledSource(),
                hosting=hosting,
                redefine=True,
            )

        problem = result.unwrap_err()[0]
        assert isinstance(problem, workspace_errors.OrphanedBaseline)
        assert problem.destination == "@/installed"
        assert not hosting.requests
        assert not emitter.cells
        assert config.read_text() == configuration
        assert lockfile.read_text() == recorded

    @pytest.mark.parametrize("state", ["missing_file", "empty_file", "missing_entry"])
    def test_missing_baseline_fails_before_acquisition(
        self, tmp_path: Path, emitter: CollectingEmitter, state: str
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        definition = 'version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "source"\n'
        configuration = definition + (
            '\n[[entries]]\ndependency = "ci"\nsource_path = "@/guide"\ndestination = "@/installed"\n'
        )
        config.write_text(configuration)
        lockfile = tmp_path / "vcsinstall.lock"
        recorded: str | None = None
        if state != "missing_file":
            recorded = "version = 1\n" if state == "empty_file" else definition + 'revision = "recorded-revision"\n'
            lockfile.write_text(recorded)
        hosting = ControlledHosting(tmp_path / "source")
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()

        with workspace.scope():
            result = dependency_add(
                DependencyName("ci"),
                make.dependency(vcs="noop"),
                source=ControlledSource(),
                hosting=hosting,
                redefine=True,
            )

        problem = result.unwrap_err()[0]
        assert isinstance(problem, workspace_errors.MissingBaseline)
        assert problem.path == lockfile
        assert problem.dependency == "ci"
        assert problem.destination == "@/installed"
        assert not hosting.requests
        assert not emitter.cells
        assert not (tmp_path / "installed").exists()
        assert config.read_text() == configuration
        if recorded is None:
            assert not lockfile.exists()
        else:
            assert lockfile.read_text() == recorded

    @pytest.mark.parametrize("location", ["config", "lockfile"])
    def test_destination_symlinks_fail_before_acquisition(
        self, tmp_path: Path, emitter: CollectingEmitter, location: str
    ) -> None:
        definition = 'version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "source"\n'
        mapping = '\n[[entries]]\ndependency = "ci"\nsource_path = "@/guide"\n' 'destination = "@/link/installed"\n'
        configuration = definition + (mapping if location == "config" else "")
        recorded = definition + 'revision = "recorded-revision"\n'
        if location == "lockfile":
            recorded += mapping + 'kind = "absent"\n'
        config = tmp_path / "vcsinstall.toml"
        lockfile = tmp_path / "vcsinstall.lock"
        config.write_text(configuration)
        lockfile.write_text(recorded)
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        hosting = ControlledHosting(tmp_path / "source")
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()

        with workspace.scope():
            result = dependency_add(
                DependencyName("ci"),
                make.dependency(vcs="noop"),
                source=ControlledSource(),
                hosting=hosting,
                redefine=True,
            )

        assert result.is_err(path_errors.InvalidProjectPath)
        assert not hosting.requests
        assert not emitter.cells
        assert link.is_symlink()
        assert config.read_text() == configuration
        assert lockfile.read_text() == recorded
