from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core import settings
from llm_tool_cli.core.errors import EnvironmentErrors
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import PathInput, ProjectConfigPath, ProjectPathId
from llm_tool_cli.paths import errors as path_errors
from pytest_mock import MockerFixture

from vcsinstall.cache import CacheRootPath
from vcsinstall.cache import errors as cache_errors
from vcsinstall.domain import Dependency, DependencyName, RepositoryLocator, SourcePath, SourceRevision, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.filesystem import FSClient
from vcsinstall.filesystem import errors as filesystem_errors
from vcsinstall.hosting import DeliveryResult, GitHostingAdapter, PublicationRequest, PushOptions
from vcsinstall.runtime import (
    dependency_add,
    dependency_list,
    dependency_outdated,
    dependency_remove,
    entry_add,
    entry_list,
    entry_remove,
)
from vcsinstall.runtime import errors as runtime_errors
from vcsinstall.runtime.commands import _prepare_dependency_lock
from vcsinstall.runtime.tests.conftest import CollectingEmitter
from vcsinstall.sources import ResolvedSource, SourceAdapter
from vcsinstall.sources import errors as source_errors
from vcsinstall.sources import get_adapter
from vcsinstall.sources.adapters.noop import NoopSourceAdapter
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import Config, DependencyLock, Entry, Lockfile, LockfilePath, Workspace
from vcsinstall.workspace import errors as workspace_errors
from vcsinstall.workspace import load_workspace


class ControlledHosting:
    __slots__ = ("path", "requests", "revisions")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[Dependency] = []
        self.revisions: list[SourceRevision | None] = []

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        raise AssertionError("Runtime must not probe repository input.")

    def validate_artifact(self, dependency: Dependency, path: Path | None) -> Result[None]:
        raise AssertionError("Acquisition must not validate publication artifacts.")

    def validate_options(self, dependency: Dependency, options: PushOptions) -> Result[None]:
        raise AssertionError("Acquisition must not validate publication options.")

    def publish(self, request: PublicationRequest, *, source: SourceAdapter) -> Result[DeliveryResult]:
        raise AssertionError("Acquisition must not publish changes.")

    @contextmanager
    def acquire(
        self, dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> Iterator[Result[Path]]:
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
class TestEntryRemove:
    @pytest.mark.parametrize("destinations", [(), (ProjectPathId("@/undeclared"),)])
    def test_invalid_selection_returns_errors_without_effects_or_output(
        self,
        tmp_path: Path,
        emitter: CollectingEmitter,
        destinations: tuple[ProjectPathId, ...],
        isolated_settings: None,
    ) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()

        with workspace.scope():
            result = entry_remove(destinations, keep_files=True)

        assert result.is_err(InvalidArguments)
        assert not emitter.cells
        assert config.read_text() == "version = 1\n"
        assert not (tmp_path / "vcsinstall.lock").exists()


@pytest.mark.usefixtures("isolated_settings")
class TestEntryAdd:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    def test_proposed_destination_is_checked_before_acquisition(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        definition = 'version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "Fixture"\n'
        config.write_text(definition)
        (tmp_path / "vcsinstall.lock").write_text(definition + 'revision = "controlled revision"\n')
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        get_source = mocker.patch(
            "vcsinstall.runtime.commands.get_adapter", side_effect=AssertionError("Invalid destination must be local")
        )
        get_hosting = mocker.patch(
            "vcsinstall.runtime.commands.get_hosting", side_effect=AssertionError("Invalid destination must be local")
        )
        entry = Entry.model_validate({"dependency": "ci", "source_path": "@/guide", "destination": "@/link/installed"})

        with workspace.scope():
            result = entry_add(entry)

        assert result.is_err(path_errors.InvalidProjectPath)
        get_source.assert_not_called()
        get_hosting.assert_not_called()
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
        get_source = mocker.patch("vcsinstall.runtime.commands.get_adapter", return_value=source)
        get_hosting = mocker.patch("vcsinstall.runtime.commands.get_hosting", return_value=Ok(hosting))
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
            entry_add(entry).unwrap()

        assert hosting.requests == [make.dependency(vcs="noop", repository="Fixture")]
        get_source.assert_called_once_with(VcsId.noop)
        get_hosting.assert_called_once_with(hosting.requests[0])
        assert hosting.revisions == [SourceRevision("controlled revision")]
        assert inspect.call_args.args[0].revision == "controlled revision"
        assert (tmp_path / "installed").read_bytes() == b"selected"
        assert len(emitter.cells) == 1
        assert emitter.cells[0].render_automation()[0].meta["operation"] == "add"

    def test_missing_lock_precedes_source_acquisition(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text('version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "Fixture"\n')
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        mocker.patch("vcsinstall.runtime.commands.get_adapter", return_value=ControlledSource())
        get_hosting = mocker.patch(
            "vcsinstall.runtime.commands.get_hosting",
            side_effect=AssertionError("Missing lock must not select hosting"),
        )
        entry = Entry.model_validate({"dependency": "ci", "source_path": "@/guide", "destination": "@/installed"})

        with workspace.scope():
            result = entry_add(entry)

        assert result.is_err(workspace_errors.DependencyLockRequired)
        get_hosting.assert_not_called()
        assert not emitter.cells
        assert not (tmp_path / "vcsinstall.lock").exists()

    def test_hosting_selection_failure_preserves_error_and_project_state(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text('version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "Fixture"\n')
        lockfile = tmp_path / "vcsinstall.lock"
        lockfile.write_text(config.read_text() + 'revision = "controlled revision"\n')
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        cause = ValueError("Unsupported hosting")
        problem = InvalidArguments(reason="No compatible hosting provider.").with_cause(cause)
        mocker.patch("vcsinstall.runtime.commands.get_adapter", return_value=ControlledSource())
        mocker.patch("vcsinstall.runtime.commands.get_hosting", return_value=Err([problem]))
        entry = Entry.model_validate({"dependency": "ci", "source_path": "@/guide", "destination": "@/installed"})
        before = config.read_bytes(), lockfile.read_bytes()

        with workspace.scope():
            result = entry_add(entry)

        assert result.unwrap_err() == [problem]
        assert result.unwrap_err()[0].cause is cause
        assert (config.read_bytes(), lockfile.read_bytes()) == before
        assert not (tmp_path / "installed").exists()
        assert not emitter.cells


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


@pytest.mark.usefixtures("isolated_settings")
class TestDependencyRemove:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.mark.parametrize("locked", [False, True])
    def test_preserves_normalization_errors_without_mutation_or_output(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture, locked: bool
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        definition = 'version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "source"\n'
        config.write_text(definition)
        lockfile = tmp_path / "vcsinstall.lock"
        recorded = definition + 'revision = "recorded-revision"\n'
        if locked:
            lockfile.write_text(recorded)
        workspace = load_workspace(cwd=PathInput(tmp_path), path=ProjectConfigPath(config)).unwrap()
        problem = path_errors.PathResolutionFailed(path="source", reason="Cannot resolve repository identity.")
        failures: EnvironmentErrors = [problem]
        source = ControlledSource()
        mocker.patch.object(source, "normalize_repository", return_value=Err(failures))
        mocker.patch("vcsinstall.runtime.commands.get_adapter", return_value=source)

        with workspace.scope():
            result = dependency_remove(DependencyName("ci"))

        assert result.unwrap_err() is failures
        assert not emitter.cells
        assert config.read_text() == definition
        assert lockfile.exists() == locked
        if locked:
            assert lockfile.read_text() == recorded


@pytest.mark.usefixtures("isolated_settings")
class TestDependencyOutdated:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Workspace:
        definition = make.dependency(vcs="noop", repository="Fixture")
        names = (DependencyName("z-last"), DependencyName("a-first"))
        return Workspace(
            config_path=ProjectConfigPath(tmp_path / "vcsinstall.toml"),
            lockfile_path=LockfilePath(tmp_path / "vcsinstall.lock"),
            config=Config(version=1, dependencies=dict.fromkeys((*names, DependencyName("unselected")), definition)),
            lockfile=Lockfile(
                version=1,
                dependencies={
                    name: DependencyLock(**definition.model_dump(), revision=SourceRevision("previous revision"))
                    for name in names
                },
            ),
        )

    def test_independent_adapters_check_only_selected_dependencies_without_content_access(
        self, workspace: Workspace, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        source = ControlledSource()
        hosting = ControlledHosting(tmp_path / "fixture")
        resolve = mocker.spy(source, "resolve")
        inspect = mocker.spy(source, "inspect")
        copy = mocker.spy(source, "copy_file")
        before = workspace.model_dump()
        mocker.patch("vcsinstall.runtime.commands.get_adapter", return_value=source)
        get_hosting = mocker.patch("vcsinstall.runtime.commands.get_hosting", return_value=Ok(hosting))

        with workspace.scope():
            dependency_outdated(
                (DependencyName("z-last"), DependencyName("a-first"), DependencyName("z-last")),
            ).unwrap()

        records = [cell.render_automation()[0].meta for cell in emitter.cells]
        assert [record["dependency"] for record in records] == ["a-first", "z-last"]
        assert all(record["revision"] == "previous revision" for record in records)
        assert all(record["current_revision"] == "controlled revision" for record in records)
        assert hosting.requests == [workspace.config.dependencies[DependencyName("a-first")]] * 2
        assert [call.args[0] for call in get_hosting.call_args_list] == hosting.requests
        assert hosting.revisions == [None, None]
        assert resolve.call_count == 2
        assert all(call.args[1] == hosting.path for call in resolve.call_args_list)
        inspect.assert_not_called()
        copy.assert_not_called()
        assert workspace.model_dump() == before
        assert not tuple(tmp_path.iterdir())

    @pytest.mark.parametrize("stage", ["normalization", "validation", "hosting", "acquisition", "resolution"])
    def test_failure_retains_source_errors_and_causes_with_dependency_context(
        self, workspace: Workspace, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture, stage: str
    ) -> None:
        source = ControlledSource()
        hosting = ControlledHosting(tmp_path / "fixture")
        original = PermissionError("Permission denied")
        problem = source_errors.RepositoryAccess(
            vcs=VcsId.noop, repository=RepositoryLocator("Fixture"), reason="Permission denied"
        ).with_cause(original)
        problems: EnvironmentErrors = [problem, path_errors.PathResolutionFailed(path="Fixture", reason="unavailable")]
        mocker.patch("vcsinstall.runtime.commands.get_adapter", return_value=source)
        mocker.patch(
            "vcsinstall.runtime.commands.get_hosting",
            return_value=Err(problems) if stage == "hosting" else Ok(hosting),
        )

        @contextmanager
        def acquire(
            dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
        ) -> Iterator[Result[Path]]:
            yield Err(problems)

        if stage == "normalization":
            mocker.patch.object(source, "normalize_repository", return_value=Err(problems))
        elif stage == "validation":
            mocker.patch.object(source, "validate_input", return_value=Err(problems))
        elif stage == "acquisition":
            mocker.patch.object(type(hosting), "acquire", side_effect=acquire)
        elif stage == "resolution":
            mocker.patch.object(source, "resolve", return_value=Err(problems))

        with workspace.scope():
            result = dependency_outdated((DependencyName("a-first"),))

        failures = result.unwrap_err()
        assert isinstance(failures[0], runtime_errors.DependencyCheckFailed)
        assert failures[0].dependency == "a-first"
        assert failures[1:] == problems
        assert failures[1].cause is original
        assert not emitter.cells

    def test_unknown_names_are_rejected_before_adapters_are_selected(
        self, workspace: Workspace, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        get_source = mocker.patch(
            "vcsinstall.runtime.commands.get_adapter", side_effect=AssertionError("Unexpected source selection")
        )
        get_hosting = mocker.patch(
            "vcsinstall.runtime.commands.get_hosting", side_effect=AssertionError("Unexpected hosting selection")
        )

        with workspace.scope():
            result = dependency_outdated((DependencyName("a-first"), DependencyName("unknown")))

        assert result.is_err(InvalidArguments)
        get_source.assert_not_called()
        get_hosting.assert_not_called()
        assert not emitter.cells


class TestEntryList:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Workspace:
        name = DependencyName("ci")
        return Workspace(
            config_path=ProjectConfigPath(tmp_path / "vcsinstall.toml"),
            lockfile_path=LockfilePath(tmp_path / "vcsinstall.lock"),
            config=Config(
                version=1,
                dependencies={name: make.dependency(vcs="noop")},
                entries=tuple(
                    Entry.model_validate({"dependency": name, "source_path": "@/file", "destination": destination})
                    for destination in ("@/a-local", "@/z-local")
                ),
            ),
            lockfile=None,
        )

    def test_preserves_inspection_errors_and_private_causes_without_partial_results(
        self, workspace: Workspace, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        original = PermissionError("Permission denied")
        problem = filesystem_errors.AccessFailed(path=workspace.project_root / "z-local", reason="Permission denied")
        failures: EnvironmentErrors = [problem.with_cause(original), path_errors.InvalidProjectPath(path="@/z-local")]
        inspect = FSClient.inspect_tree
        mocker.patch.object(
            FSClient,
            "inspect_tree",
            autospec=True,
            side_effect=lambda client, path: Err(failures) if path == "@/z-local" else inspect(client, path),
        )

        with workspace.scope():
            result = entry_list()

        assert result.unwrap_err() is failures
        assert result.unwrap_err()[0].cause is original
        assert not emitter.cells

    def test_preserves_repository_normalization_failures(
        self, workspace: Workspace, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        problem = path_errors.PathResolutionFailed(path="source", reason="Cannot resolve repository identity.")
        failures: EnvironmentErrors = [problem]
        source = ControlledSource()
        mocker.patch.object(source, "normalize_repository", return_value=Err(failures))
        mocker.patch("vcsinstall.runtime.commands.get_adapter", return_value=source)

        with workspace.scope():
            result = entry_list()

        assert result.unwrap_err() is failures
        assert not emitter.cells


class TestDependencyList:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.mark.parametrize("locked", [False, True])
    def test_preserves_repository_normalization_failures(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture, locked: bool
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        definition = 'version = 1\n[dependencies.ci]\nvcs = "noop"\nrepository = "source"\n'
        config.write_text(definition)
        lockfile = tmp_path / "vcsinstall.lock"
        if locked:
            lockfile.write_text(definition + 'revision = "recorded-revision"\n')
        workspace = load_workspace(cwd=PathInput(tmp_path)).unwrap()
        problem = path_errors.PathResolutionFailed(path="source", reason="Cannot resolve repository identity.")
        failures: EnvironmentErrors = [problem]
        source = ControlledSource()
        mocker.patch.object(source, "normalize_repository", return_value=Err(failures))
        mocker.patch("vcsinstall.runtime.commands.get_adapter", return_value=source)

        with workspace.scope():
            result = dependency_list()

        assert result.unwrap_err() is failures
        assert not emitter.cells
        assert config.read_text() == definition
        assert lockfile.exists() == locked

    def test_preserves_inspection_diagnostics_and_emits_no_partial_results(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        name = DependencyName("z-inspected")
        workspace = Workspace(
            config_path=ProjectConfigPath(tmp_path / "vcsinstall.toml"),
            lockfile_path=LockfilePath(tmp_path / "vcsinstall.lock"),
            config=Config(
                version=1,
                dependencies={key: make.dependency(vcs="noop") for key in (DependencyName("a-empty"), name)},
                entries=(
                    Entry.model_validate({"dependency": name, "source_path": "@/file", "destination": "@/local"}),
                ),
            ),
            lockfile=None,
        )
        problem = filesystem_errors.AccessFailed(path=tmp_path / "local", reason="Permission denied")
        original = PermissionError("Permission denied")
        problem = problem.with_cause(original)
        failures: EnvironmentErrors = [problem, path_errors.InvalidProjectPath(path="@/local")]
        mocker.patch("vcsinstall.filesystem.FSClient.inspect_tree", return_value=Err(failures))

        with workspace.scope():
            result = dependency_list()

        assert result.unwrap_err() is failures
        assert result.unwrap_err()[0].cause is original
        assert not emitter.cells
        assert not tuple(tmp_path.iterdir())
