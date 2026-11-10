from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest
import tomli_w
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.entities import ExitCode
from llm_tool_cli.core.errors import exit_code_for_errors
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.output.tests.helpers import CollectingEmitter
from llm_tool_cli.paths import ProjectConfigPath, ProjectRootPath
from pytest_mock import MockerFixture

from vcsinstall.domain import (
    Dependency,
    DependencyName,
    RepositoryLocator,
    SourcePath,
    SourceRevision,
    VcsId,
    VersionSelector,
)
from vcsinstall.domain.tests import make
from vcsinstall.filesystem import ContentTree, FSClient, hashes
from vcsinstall.runtime import errors, install
from vcsinstall.sources import ResolvedSource
from vcsinstall.sources import errors as source_errors
from vcsinstall.sources.adapters.noop import NoopSourceAdapter
from vcsinstall.workspace import Config, DependencyLock, Entry, FileBaseline, Lockfile, LockfilePath, Workspace


class ControlledHosting:
    __slots__ = ("path", "active", "requests")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.active = 0
        self.requests: list[tuple[Dependency, SourceRevision | None, bool]] = []

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        raise AssertionError("Installation must not probe declared repositories.")

    @contextmanager
    def acquire(
        self, dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> Iterator[Result[Path]]:
        self.requests.append((dependency, revision, refresh))
        self.active += 1
        try:
            yield Ok(self.path)
        finally:
            self.active -= 1


class ControlledSource(NoopSourceAdapter):
    __slots__ = ()

    def resolve(self, dependency: Dependency, path: Path) -> Result[ResolvedSource]:
        return Ok(ResolvedSource(dependency=dependency, path=path, revision=SourceRevision("new")))

    def inspect(self, source: ResolvedSource, paths: tuple[SourcePath, ...]) -> Result[dict[SourcePath, ContentTree]]:
        filesystem = FSClient(root=ProjectRootPath(source.path))
        return Ok({path: filesystem.inspect_tree(path).unwrap() for path in paths})

    def copy_file(self, source: ResolvedSource, source_path: SourcePath, output: BinaryIO) -> Result[None]:
        return FSClient(root=ProjectRootPath(source.path)).copy_file(source_path, output)


class TestInstall:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Workspace:
        names = (DependencyName("a"), DependencyName("z"))
        definition = make.dependency(vcs="noop", repository="Fixture")
        entries = tuple(
            Entry.model_validate({"dependency": name, "source_path": "@/guide", "destination": "@/" + name})
            for name in names
        )
        workspace = Workspace(
            config_path=ProjectConfigPath(tmp_path / "vcsinstall.toml"),
            lockfile_path=LockfilePath(tmp_path / "vcsinstall.lock"),
            config=Config(version=1, dependencies=dict.fromkeys(names, definition), entries=entries),
            lockfile=Lockfile(
                version=1,
                dependencies={
                    name: DependencyLock(
                        **definition.replace(repository=RepositoryLocator("Old Fixture")).model_dump(),
                        revision=SourceRevision("old"),
                    )
                    for name in names
                },
                entries=tuple(FileBaseline(**entry.model_dump(), hash=hashes.hash_file(b"old")) for entry in entries),
            ),
        )
        workspace.config_path.write_text(tomli_w.dumps(workspace.config.model_dump(mode="json", exclude_none=True)))
        assert workspace.lockfile is not None
        workspace.lockfile_path.write_text(
            tomli_w.dumps(workspace.lockfile.model_dump(mode="json", exclude_none=True))
        )
        for name in names:
            (tmp_path / name).write_bytes(b"old")
        return workspace

    @pytest.fixture
    def hosting(self, tmp_path: Path) -> ControlledHosting:
        directory = tmp_path / "source"
        directory.mkdir()
        (directory / "guide").write_bytes(b"new")
        return ControlledHosting(directory)

    def test_resolves_once_per_changed_binding_and_keeps_scopes_open_through_copying(
        self, workspace: Workspace, hosting: ControlledHosting, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        source = ControlledSource()
        resolve = mocker.spy(source, "resolve")
        inspect = mocker.spy(source, "inspect")
        copy = source.copy_file
        mocker.patch("vcsinstall.runtime.installations.get_adapter", return_value=source)
        get_hosting = mocker.patch("vcsinstall.runtime.installations.get_hosting", return_value=Ok(hosting))

        def copy_with_active_scopes(resolved: ResolvedSource, path: SourcePath, stream: BinaryIO) -> Result[None]:
            assert hosting.active == 2
            return copy(resolved, path, stream)

        mocker.patch.object(source, "copy_file", side_effect=copy_with_active_scopes)
        with workspace.scope():
            install().unwrap()

        assert resolve.call_count == 2
        assert inspect.call_count == 2
        assert hosting.requests == [(definition, None, True) for definition in workspace.config.dependencies.values()]
        assert [call.args[0] for call in get_hosting.call_args_list] == [request[0] for request in hosting.requests]
        assert hosting.active == 0
        assert (workspace.project_root / "a").read_bytes() == b"new"
        assert (workspace.project_root / "z").read_bytes() == b"new"
        assert len(emitter.cells) == 4

    @pytest.mark.usefixtures("emitter")
    @pytest.mark.parametrize("locked", [False, True])
    @pytest.mark.parametrize(
        "selector",
        [None, make.selector("branch", "release"), make.selector("tag", "v1"), make.selector("revision", "old")],
    )
    def test_forced_restoration_preserves_binding_and_requests_locked_revision_without_refresh(
        self,
        workspace: Workspace,
        hosting: ControlledHosting,
        mocker: MockerFixture,
        selector: VersionSelector | None,
        locked: bool,
    ) -> None:
        definitions = {
            name: definition.replace(selector=selector) for name, definition in workspace.config.dependencies.items()
        }
        assert workspace.lockfile is not None
        workspace = workspace.replace(
            config=workspace.config.replace(dependencies=definitions),
            lockfile=workspace.lockfile.replace(
                dependencies={
                    name: DependencyLock(**definition.model_dump(), revision=SourceRevision("old"))
                    for name, definition in definitions.items()
                }
            ),
        )
        for name in definitions:
            (workspace.project_root / name).unlink()
        (hosting.path / "guide").write_bytes(b"old")
        source = ControlledSource()
        resolve = mocker.spy(source, "resolve")
        mocker.patch("vcsinstall.runtime.installations.get_adapter", return_value=source)
        mocker.patch("vcsinstall.runtime.installations.get_hosting", return_value=Ok(hosting))

        with workspace.scope():
            install(locked=locked, force=True).unwrap()

        resolve.assert_not_called()
        assert hosting.requests == [(definition, SourceRevision("old"), False) for definition in definitions.values()]
        for name in definitions:
            assert (workspace.project_root / name).read_bytes() == b"old"

    @pytest.mark.parametrize(
        "stage", ["normalization", "input", "hosting", "acquisition", "resolution", "revision", "inspection"]
    )
    def test_preparation_failure_preserves_original_errors_and_releases_scopes(
        self,
        workspace: Workspace,
        hosting: ControlledHosting,
        emitter: CollectingEmitter,
        mocker: MockerFixture,
        stage: str,
    ) -> None:
        source = ControlledSource()
        cause = PermissionError("unreadable source")
        problem = source_errors.RepositoryAccess(
            vcs=VcsId.noop, repository=RepositoryLocator("Fixture"), reason="unreadable source"
        ).with_cause(cause)
        mocker.patch("vcsinstall.runtime.installations.get_adapter", return_value=source)
        mocker.patch(
            "vcsinstall.runtime.installations.get_hosting",
            return_value=Err([problem]) if stage == "hosting" else Ok(hosting),
        )

        @contextmanager
        def acquire(
            dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
        ) -> Iterator[Result[Path]]:
            yield Err([problem])

        if stage == "acquisition":
            mocker.patch.object(ControlledHosting, "acquire", side_effect=acquire)
        elif stage != "hosting":
            method = {
                "normalization": "normalize_repository",
                "input": "validate_input",
                "resolution": "resolve",
                "revision": "validate_revision_lock",
                "inspection": "inspect",
            }[stage]
            mocker.patch.object(source, method, return_value=Err([problem]))
        before = workspace.config_path.read_bytes(), workspace.lockfile_path.read_bytes()

        with workspace.scope():
            result = install()

        failures = result.unwrap_err()
        assert isinstance(failures[0], errors.DependencyInstallationFailed)
        assert failures[0].dependency == "a"
        assert failures[1:] == [problem]
        assert failures[1].cause is cause
        assert hosting.active == 0
        assert not emitter.cells
        assert (workspace.config_path.read_bytes(), workspace.lockfile_path.read_bytes()) == before
        assert (workspace.project_root / "a").read_bytes() == b"old"
        assert (workspace.project_root / "z").read_bytes() == b"old"

    def test_copy_failure_keeps_prior_records_and_reports_possible_inconsistency(
        self, workspace: Workspace, hosting: ControlledHosting, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        source = ControlledSource()
        problem = source_errors.RepositoryAccess(
            vcs=VcsId.noop, repository=RepositoryLocator("Fixture"), reason="stream failed"
        )
        mocker.patch("vcsinstall.runtime.installations.get_adapter", return_value=source)
        mocker.patch("vcsinstall.runtime.installations.get_hosting", return_value=Ok(hosting))
        copy = source.copy_file
        copied = False

        def fail_second_copy(resolved: ResolvedSource, path: SourcePath, stream: BinaryIO) -> Result[None]:
            nonlocal copied
            if copied:
                return Err([problem])
            copied = True
            return copy(resolved, path, stream)

        mocker.patch.object(source, "copy_file", side_effect=fail_second_copy)
        before = workspace.config_path.read_bytes(), workspace.lockfile_path.read_bytes()

        with workspace.scope():
            result = install()

        failures = result.unwrap_err()
        assert isinstance(failures[0], errors.DependencyInstallationFailed)
        assert failures[0].dependency == "z"
        assert any(isinstance(failure, errors.InstallationWriteFailed) for failure in failures)
        assert failures[-1] is problem
        assert (workspace.config_path.read_bytes(), workspace.lockfile_path.read_bytes()) == before
        assert (workspace.project_root / "a").read_bytes() == b"new"
        assert hosting.active == 0
        assert not emitter.cells

    def test_shared_invalid_arguments_retain_their_exit_category_and_cause(
        self, workspace: Workspace, hosting: ControlledHosting, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        source = ControlledSource()
        cause = ValueError("unsupported selection")
        problem = InvalidArguments(reason="unsupported selection").with_cause(cause)
        mocker.patch("vcsinstall.runtime.installations.get_adapter", return_value=source)
        mocker.patch("vcsinstall.runtime.installations.get_hosting", return_value=Ok(hosting))
        mocker.patch.object(source, "validate_input", return_value=Err([problem]))

        with workspace.scope():
            result = install()

        failures = result.unwrap_err()
        assert exit_code_for_errors(failures) == ExitCode.invalid_arguments
        assert failures[-1] is problem
        assert failures[-1].cause is cause
        assert not hosting.requests
        assert not emitter.cells
