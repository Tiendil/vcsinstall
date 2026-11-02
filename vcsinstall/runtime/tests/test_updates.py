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
from llm_tool_cli.paths import ProjectConfigPath, ProjectPathId, ProjectRootPath
from pytest_mock import MockerFixture

from vcsinstall.domain import Dependency, DependencyName, RepositoryLocator, SourcePath, SourceRevision, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.filesystem import ContentTree, FSClient, hashes
from vcsinstall.runtime import dependency_update
from vcsinstall.runtime import errors as runtime_errors
from vcsinstall.runtime.tests.conftest import CollectingEmitter
from vcsinstall.sources import ResolvedSource
from vcsinstall.sources import errors as source_errors
from vcsinstall.sources.adapters.noop import NoopSourceAdapter
from vcsinstall.workspace import Config, DependencyLock, Entry, FileBaseline, Lockfile, LockfilePath, Workspace


class ScopedHosting:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.active = 0
        self.requests: list[Dependency] = []

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        raise AssertionError("Updates must not probe declared repositories.")

    @contextmanager
    def acquire(
        self, dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> Iterator[Result[Path]]:
        self.requests.append(dependency)
        self.active += 1
        try:
            yield Ok(self.path)
        finally:
            self.active -= 1


class FixtureSource(NoopSourceAdapter):
    def resolve(self, dependency: Dependency, path: Path) -> Result[ResolvedSource]:
        return Ok(ResolvedSource(dependency=dependency, path=path, revision=SourceRevision("new")))

    def inspect(self, source: ResolvedSource, paths: tuple[SourcePath, ...]) -> Result[dict[SourcePath, ContentTree]]:
        filesystem = FSClient(root=ProjectRootPath(source.path))
        return Ok({path: filesystem.inspect_tree(path).unwrap() for path in paths})

    def copy_file(self, source: ResolvedSource, source_path: SourcePath, output: BinaryIO) -> Result[None]:
        return FSClient(root=ProjectRootPath(source.path)).copy_file(source_path, output)


class TestDependencyUpdate:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Workspace:
        definition = make.dependency(vcs="noop", repository="Fixture")
        names = (DependencyName("a"), DependencyName("z"))
        entries = tuple(
            Entry(
                dependency=name,
                source_path=SourcePath(ProjectPathId("@/guide")),
                destination=ProjectPathId("@/" + name),
            )
            for name in names
        )
        workspace = Workspace(
            config_path=ProjectConfigPath(tmp_path / "vcsinstall.toml"),
            lockfile_path=LockfilePath(tmp_path / "vcsinstall.lock"),
            config=Config(version=1, dependencies=dict.fromkeys(names, definition), entries=entries),
            lockfile=Lockfile(
                version=1,
                dependencies={
                    name: DependencyLock(**definition.model_dump(), revision=SourceRevision("old")) for name in names
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
    def hosting(self, tmp_path: Path) -> ScopedHosting:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide").write_bytes(b"new")
        return ScopedHosting(source)

    def test_resolves_once_per_dependency_and_keeps_acquisitions_open_through_copying(
        self, workspace: Workspace, hosting: ScopedHosting, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        source = FixtureSource()
        resolve = mocker.spy(source, "resolve")
        copy = source.copy_file

        def copy_with_active_scopes(resolved: ResolvedSource, path: SourcePath, stream: BinaryIO) -> Result[None]:
            assert hosting.active == 2
            assert resolved.revision == "new"
            return copy(resolved, path, stream)

        mocker.patch.object(source, "copy_file", side_effect=copy_with_active_scopes)
        with workspace.scope():
            dependency_update(source_for=lambda vcs: source, hosting_for=lambda vcs: Ok(hosting)).unwrap()

        assert resolve.call_count == 2
        assert len(hosting.requests) == 2
        assert hosting.active == 0
        assert (workspace.project_root / "a").read_bytes() == b"new"
        assert (workspace.project_root / "z").read_bytes() == b"new"
        assert len(emitter.cells) == 4

    @pytest.mark.parametrize(
        "stage", ["normalization", "input", "hosting", "acquisition", "resolution", "revision", "inspection"]
    )
    def test_preparation_failures_preserve_original_errors_and_release_acquisitions(
        self,
        workspace: Workspace,
        hosting: ScopedHosting,
        emitter: CollectingEmitter,
        mocker: MockerFixture,
        stage: str,
    ) -> None:
        source = FixtureSource()
        cause = PermissionError("unreadable source")
        problem = source_errors.RepositoryAccess(
            vcs=VcsId.noop, repository=RepositoryLocator("Fixture"), reason="unreadable source"
        ).with_cause(cause)

        @contextmanager
        def acquire(
            dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
        ) -> Iterator[Result[Path]]:
            yield Err([problem])

        if stage == "acquisition":
            mocker.patch.object(hosting, "acquire", side_effect=acquire)
        elif stage != "hosting":
            method = {
                "normalization": "normalize_repository",
                "input": "validate_input",
                "resolution": "resolve",
                "revision": "validate_revision_lock",
                "inspection": "inspect",
            }[stage]
            mocker.patch.object(source, method, return_value=Err([problem]))
        before = (workspace.config_path.read_bytes(), workspace.lockfile_path.read_bytes())

        with workspace.scope():
            result = dependency_update(
                source_for=lambda vcs: source,
                hosting_for=lambda vcs: Err([problem]) if stage == "hosting" else Ok(hosting),
            )

        failures = result.unwrap_err()
        assert isinstance(failures[0], runtime_errors.DependencyUpdateFailed)
        assert failures[0].dependency == "a"
        assert failures[1:] == [problem]
        assert failures[1].cause is cause
        assert hosting.active == 0
        assert not emitter.cells
        assert (workspace.config_path.read_bytes(), workspace.lockfile_path.read_bytes()) == before
        assert (workspace.project_root / "a").read_bytes() == b"old"
        assert (workspace.project_root / "z").read_bytes() == b"old"

    def test_copy_failure_retains_prior_locks_and_reports_possible_inconsistency(
        self, workspace: Workspace, hosting: ScopedHosting, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        source = FixtureSource()
        problem = source_errors.RepositoryAccess(
            vcs=VcsId.noop, repository=RepositoryLocator("Fixture"), reason="cannot stream content"
        )
        copy = source.copy_file
        copied = False

        def fail_second_copy(resolved: ResolvedSource, path: SourcePath, stream: BinaryIO) -> Result[None]:
            nonlocal copied
            if copied:
                return Err([problem])
            copied = True
            return copy(resolved, path, stream)

        mocker.patch.object(source, "copy_file", side_effect=fail_second_copy)
        before = (workspace.config_path.read_bytes(), workspace.lockfile_path.read_bytes())

        with workspace.scope():
            result = dependency_update(source_for=lambda vcs: source, hosting_for=lambda vcs: Ok(hosting))

        failures = result.unwrap_err()
        assert isinstance(failures[0], runtime_errors.DependencyUpdateFailed)
        assert failures[0].dependency == "z"
        assert any(isinstance(failure, runtime_errors.InstallationWriteFailed) for failure in failures)
        assert failures[-1] == problem
        assert (workspace.config_path.read_bytes(), workspace.lockfile_path.read_bytes()) == before
        assert (workspace.project_root / "a").read_bytes() == b"new"
        assert hosting.active == 0
        assert not emitter.cells

    def test_source_argument_failures_keep_shared_exit_category_and_original_error(
        self, workspace: Workspace, hosting: ScopedHosting, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        source = FixtureSource()
        cause = ValueError("unsupported selection")
        problem = InvalidArguments(reason="unsupported selection").with_cause(cause)
        mocker.patch.object(source, "validate_input", return_value=Err([problem]))

        with workspace.scope():
            result = dependency_update(source_for=lambda vcs: source, hosting_for=lambda vcs: Ok(hosting))

        failures = result.unwrap_err()
        assert exit_code_for_errors(failures) == ExitCode.invalid_arguments
        assert failures[-1] is problem
        assert failures[-1].cause is cause
        assert "a" in failures[0].format_message()
        assert not hosting.requests
        assert not emitter.cells
