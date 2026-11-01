from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import PathInput, ProjectConfigPath
from llm_tool_cli.paths import errors as path_errors
from pytest_mock import MockerFixture

from vcsinstall.cache import CacheRootPath
from vcsinstall.cache import errors as cache_errors
from vcsinstall.domain import Dependency, DependencyName, RepositoryLocator, SourcePath, SourceRevision, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.hosting import GitHostingAdapter
from vcsinstall.runtime import dependency_add, entry_add
from vcsinstall.runtime.commands import _prepare_dependency_lock
from vcsinstall.runtime.tests.conftest import CollectingEmitter
from vcsinstall.sources import ResolvedSource
from vcsinstall.sources import errors as source_errors
from vcsinstall.sources import get_adapter
from vcsinstall.sources.adapters.noop import NoopSourceAdapter
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import Config, DependencyLock, Entry, Lockfile, LockfilePath, Workspace
from vcsinstall.workspace import errors as workspace_errors
from vcsinstall.workspace import load_workspace


class ControlledHosting:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[Dependency] = []
        self.revisions: list[SourceRevision | None] = []

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        raise AssertionError("Runtime must not probe repository input.")

    @contextmanager
    def acquire(self, dependency: Dependency, *, revision: SourceRevision | None = None) -> Iterator[Result[Path]]:
        self.requests.append(dependency)
        self.revisions.append(revision)
        yield Ok(self.path)


class ControlledSource(NoopSourceAdapter):
    def resolve(self, dependency: Dependency, path: Path) -> Result[ResolvedSource]:
        return Ok(ResolvedSource(dependency=dependency, path=path, revision=SourceRevision("controlled revision")))


class TestPrepareDependencyLock:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Workspace:
        name = DependencyName("ci")
        declaration = make.dependency(repository="../source", selector=make.selector("branch", "trunk"))
        recorded = DependencyLock(
            **declaration.replace(repository=RepositoryLocator(str(tmp_path / "source"))).model_dump(),
            revision=SourceRevision("1" * 40),
        )
        return Workspace(
            config_path=ProjectConfigPath(tmp_path / "project/vcsinstall.toml"),
            lockfile_path=LockfilePath(tmp_path / "project/vcsinstall.lock"),
            config=Config(version=1, dependencies={name: declaration}),
            lockfile=Lockfile(version=1, dependencies={name: recorded}),
        )

    def test_prepares_equivalent_locators_without_changing_loaded_models(self, workspace: Workspace) -> None:
        before = workspace.model_dump()

        locked = _prepare_dependency_lock(workspace, DependencyName("ci"), source=get_adapter(VcsId.git)).unwrap()

        assert locked.repository == str(workspace.project_root.parent / "source")
        assert locked.revision == "1" * 40
        assert workspace.model_dump() == before

    @pytest.mark.parametrize("rejected", [False, True])
    def test_locator_rejection_and_operational_errors_are_preserved(
        self, workspace: Workspace, mocker: MockerFixture, rejected: bool
    ) -> None:
        source = get_adapter(VcsId.git)
        problem = source_errors.RepositoryAccess(
            vcs=VcsId.git, repository=RepositoryLocator("source"), reason="unreadable"
        )
        mocker.patch.object(source, "normalize_repository", return_value=Ok(None) if rejected else Err([problem]))
        before = workspace.model_dump()

        result = _prepare_dependency_lock(workspace, DependencyName("ci"), source=source)

        if rejected:
            assert result.is_err(workspace_errors.DependencyLockRequired)
        else:
            assert result.unwrap_err() == [problem]
        assert workspace.model_dump() == before

    def test_missing_lock_does_not_normalize_locators(self, workspace: Workspace, mocker: MockerFixture) -> None:
        workspace = workspace.replace(lockfile=None)
        source = get_adapter(VcsId.git)
        normalize = mocker.patch.object(source, "normalize_repository", side_effect=AssertionError("unexpected I/O"))

        result = _prepare_dependency_lock(workspace, DependencyName("ci"), source=source)

        assert result.is_err(workspace_errors.DependencyLockRequired)
        normalize.assert_not_called()


@pytest.mark.usefixtures("isolated_settings")
class TestEntryAdd:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    def test_proposed_destination_is_checked_before_acquisition(
        self, tmp_path: Path, emitter: CollectingEmitter
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        definition = 'version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "Fixture"\n'
        config.write_text(definition)
        (tmp_path / "vcsinstall.lock").write_text(definition + 'revision = "controlled revision"\n')
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        hosting = ControlledHosting(tmp_path / "fixture")
        entry = Entry.model_validate({"dependency": "ci", "source_path": "@/guide", "destination": "@/link/installed"})

        with workspace.scope():
            result = entry_add(entry, source=ControlledSource(), hosting=hosting)

        assert result.is_err(path_errors.InvalidProjectPath)
        assert not hosting.requests
        assert not emitter.cells
        assert config.read_text() == definition

    def test_independent_hosting_and_source_receive_effective_locked_definition(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        from llm_tool_cli.paths import ProjectPathId

        from vcsinstall.domain import ContentKind
        from vcsinstall.filesystem import ContentTree, FileInfo, hashes

        config = tmp_path / "vcsinstall.toml"
        config.write_text('version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "Fixture"\n')
        lockfile = tmp_path / "vcsinstall.lock"
        lockfile.write_text(config.read_text() + 'revision = "controlled revision"\n')
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        hosting = ControlledHosting(tmp_path / "fixture")
        source = ControlledSource()
        tree = ContentTree(
            kind=ContentKind.file, files=(FileInfo(path=ProjectPathId("@/"), hash=hashes.hash_file(b"selected")),)
        )
        inspect = mocker.patch.object(source, "inspect", return_value=Ok({"@/guide": tree}))

        def copy_file(resolved: ResolvedSource, path: SourcePath, stream: BinaryIO) -> Result[None]:
            stream.write(b"selected")
            return Ok(None)

        mocker.patch.object(source, "copy_file", side_effect=copy_file)
        entry = Entry.model_validate({"dependency": "ci", "source_path": "@/guide", "destination": "@/installed"})

        with workspace.scope():
            entry_add(entry, source=source, hosting=hosting).unwrap()

        assert hosting.requests == [make.dependency(vcs="noop", repository="Fixture")]
        assert hosting.revisions == [SourceRevision("controlled revision")]
        assert inspect.call_args.args[0].revision == "controlled revision"
        assert (tmp_path / "installed").read_bytes() == b"selected"
        assert len(emitter.cells) == 1
        assert emitter.cells[0].render_automation()[0].meta["operation"] == "add"

    def test_missing_lock_precedes_source_acquisition(self, tmp_path: Path, emitter: CollectingEmitter) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text('version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "Fixture"\n')
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        hosting = ControlledHosting(tmp_path / "fixture")
        entry = Entry.model_validate({"dependency": "ci", "source_path": "@/guide", "destination": "@/installed"})

        with workspace.scope():
            result = entry_add(entry, source=ControlledSource(), hosting=hosting)

        assert result.is_err(workspace_errors.DependencyLockRequired)
        assert not hosting.requests
        assert not emitter.cells
        assert not (tmp_path / "vcsinstall.lock").exists()


@pytest.mark.usefixtures("isolated_settings")
class TestDependencyAdd:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.mark.parametrize("selected", [False, True])
    def test_only_selected_destinations_are_resolved_before_acquisition(
        self, tmp_path: Path, emitter: CollectingEmitter, selected: bool
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        definition = 'version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "Fixture"\n'
        mapping = '\n[[entries]]\ndependency = "ci"\nsource_path = "@/guide"\ndestination = "@/link/installed"\n'
        config.write_text(definition + mapping)
        lockfile = tmp_path / "vcsinstall.lock"
        lockfile.write_text(definition + 'revision = "controlled revision"\n' + mapping + 'kind = "absent"\n')
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        hosting = ControlledHosting(tmp_path / "fixture")
        name = DependencyName("ci" if selected else "other")
        prepared = make.dependency(vcs="noop", repository="Fixture")

        with workspace.scope():
            result = dependency_add(name, prepared, source=ControlledSource(), hosting=hosting, redefine=True)

        if selected:
            assert result.is_err(path_errors.InvalidProjectPath)
            assert not hosting.requests
            assert not emitter.cells
            assert config.read_text() == definition + mapping
        else:
            assert result.is_ok()
            assert hosting.requests == [prepared]
            assert len(emitter.cells) == 1
            updated = load_workspace(cwd=PathInput(tmp_path)).unwrap()
            assert updated.config.entries == workspace.config.entries
            assert updated.lockfile is not None and workspace.lockfile is not None
            assert updated.lockfile.entries == workspace.lockfile.entries

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
