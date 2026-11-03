import hashlib
import json
import os
import tomllib
import tracemalloc
from contextlib import AbstractContextManager
from pathlib import Path
from urllib.parse import quote

import pytest
import tomli_w
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core import errors as core_errors
from llm_tool_cli.core import settings
from llm_tool_cli.core.entities import ExitCode
from llm_tool_cli.core.result import Err, Ok, Result
from llm_tool_cli.paths import ProjectPathId
from llm_tool_cli.protocol.tests.helpers import cell_payloads
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cache import CacheRootPath, active_cache_root
from vcsinstall.cache import errors as cache_errors
from vcsinstall.cli import application
from vcsinstall.cli.commands import dependency
from vcsinstall.cli.commands.tests.helpers import (
    declare_entry,
    invoke_add,
    invoke_dependency_list,
    invoke_dependency_outdated,
    invoke_dependency_remove,
    invoke_dependency_update,
    invoke_entry_add,
)
from vcsinstall.domain import Dependency, DependencyName, RepositoryLocator, SelectorValue, SourceRevision, VcsId
from vcsinstall.filesystem import FSClient
from vcsinstall.filesystem import errors as filesystem_errors
from vcsinstall.filesystem import hashes
from vcsinstall.hosting import DeliveryResult, HostingAdapter, PublicationRequest, PushOptions
from vcsinstall.runtime import errors as runtime_errors
from vcsinstall.sources import DEFAULT_ADAPTERS, SourceAdapter
from vcsinstall.sources import errors as source_errors
from vcsinstall.sources import parse_repository
from vcsinstall.sources.adapters.filesystem import FilesystemSourceAdapter
from vcsinstall.sources.adapters.noop import NoopSourceAdapter
from vcsinstall.vcs_clients import GitClient, clone_mirror
from vcsinstall.vcs_clients import errors as vcs_errors
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import active_workspace
from vcsinstall.workspace import errors as workspace_errors


class ProbingHosting:
    __slots__ = ("result", "probes")

    def __init__(self, result: Result[bool] | None = None) -> None:
        self.result = result if result is not None else Ok(True)
        self.probes: list[RepositoryLocator] = []

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        self.probes.append(repository)
        return self.result

    def validate_artifact(self, dependency: Dependency, path: Path | None) -> Result[None]:
        raise AssertionError("Input preparation must not validate publication artifacts.")

    def validate_options(self, dependency: Dependency, options: PushOptions) -> Result[None]:
        raise AssertionError("Input preparation must not validate publication options.")

    def publish(self, request: PublicationRequest, *, source: SourceAdapter) -> Result[DeliveryResult]:
        raise AssertionError("Input preparation must not publish changes.")

    def acquire(
        self, dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> AbstractContextManager[Result[Path]]:
        raise AssertionError("Input preparation must not acquire a repository.")


class RecognizingSource(NoopSourceAdapter):
    def probe_repository(self, repository: RepositoryLocator, *, cwd: Path) -> Result[bool]:
        return Ok(repository == "Literal Fixture Identity")


class TestPrepareDependency:
    @pytest.mark.parametrize("locator", ["git+https://host/repository", "file:///source", "svn+ssh://host/repository"])
    def test_explicit_backend_bypasses_prefix_inference_and_probes(
        self, tmp_path: Path, locator: str, mocker: MockerFixture
    ) -> None:
        source = RecognizingSource()
        adapters = dict(DEFAULT_ADAPTERS) | {VcsId.noop: source}
        source_probe = mocker.spy(source, "probe_repository")
        hosting = ProbingHosting()

        prepared = dependency.prepare_dependency(
            parse_repository(RepositoryLocator(locator)).unwrap(),
            cwd=tmp_path,
            vcs=VcsId.noop,
            adapters=adapters,
            hosting=hosting,
        ).unwrap()

        assert prepared.vcs == VcsId.noop
        assert prepared.repository == locator
        source_probe.assert_not_called()
        assert not hosting.probes

    def test_detects_an_injected_noop_adapter(self, tmp_path: Path) -> None:
        adapters = dict(DEFAULT_ADAPTERS) | {VcsId.noop: RecognizingSource()}

        prepared = dependency.prepare_dependency(
            parse_repository(RepositoryLocator("Literal Fixture Identity")).unwrap(), cwd=tmp_path, adapters=adapters
        ).unwrap()

        assert prepared.vcs == VcsId.noop
        assert prepared.repository == "Literal Fixture Identity"

    def test_selected_adapter_rejects_unsupported_locator(self, tmp_path: Path, mocker: MockerFixture) -> None:
        normalize = mocker.spy(FilesystemSourceAdapter, "normalize_repository")
        probe = mocker.spy(FilesystemSourceAdapter, "probe_repository")
        hosting = ProbingHosting()
        locator = RepositoryLocator("git+https://host/repository")

        result = dependency.prepare_dependency(
            parse_repository(locator).unwrap(), cwd=tmp_path, vcs=VcsId.filesystem, hosting=hosting
        )

        problem = result.unwrap_err()[0]
        assert isinstance(problem, InvalidArguments)
        assert "filesystem" in problem.reason
        assert normalize.call_args.args[1] == locator
        probe.assert_not_called()
        assert not hosting.probes

    @pytest.mark.parametrize(
        "locator,vcs,probed",
        [
            ("https://example.test/shared#branch=trunk", None, True),
            ("ssh://example.test/shared", None, True),
            ("https://example.test/shared", VcsId.git, False),
            ("git+https://example.test/shared", None, False),
            ("git://example.test/shared", None, False),
            ("git@example.test:shared", None, False),
        ],
    )
    def test_remote_detection_uses_hosting_and_preserves_native_identity(
        self, tmp_path: Path, locator: str, vcs: VcsId | None, probed: bool
    ) -> None:
        request = parse_repository(RepositoryLocator(locator)).unwrap()
        hosting = ProbingHosting()

        prepared = dependency.prepare_dependency(request, cwd=tmp_path, vcs=vcs, hosting=hosting).unwrap()

        native = RepositoryLocator(locator.removeprefix("git+").split("#", 1)[0])
        assert hosting.probes == ([native] if probed else [])
        assert prepared.vcs == VcsId.git
        assert prepared.repository == native
        assert prepared.selector == request.selector

    @pytest.mark.parametrize("reason", [None, "Permission denied", "Git command timed out"])
    def test_remote_probe_rejection_and_errors(self, tmp_path: Path, reason: str | None) -> None:
        locator = RepositoryLocator("https://example.test/shared")
        problem = vcs_errors.CommandFailed(vcs=VcsId.git, repository=locator, reason=reason) if reason else None
        hosting = ProbingHosting(Err([problem]) if problem is not None else Ok(False))

        result = dependency.prepare_dependency(parse_repository(locator).unwrap(), cwd=tmp_path, hosting=hosting)

        if problem is not None:
            assert result.unwrap_err() == [problem]
        else:
            assert result.is_err(InvalidArguments)
        assert hosting.probes == [locator]
        assert not tuple(tmp_path.iterdir())

    def test_remote_normalization_rejection_skips_hosting_probe(self, tmp_path: Path, mocker: MockerFixture) -> None:
        hosting = ProbingHosting()
        mocker.patch("vcsinstall.sources.adapters.git.GitSourceAdapter.normalize_repository", return_value=Ok(None))

        result = dependency.prepare_dependency(
            parse_repository(RepositoryLocator("https://example.test/shared")).unwrap(), cwd=tmp_path, hosting=hosting
        )

        assert result.is_err(InvalidArguments)
        assert not hosting.probes


@pytest.mark.usefixtures("isolated_settings")
class TestAddDependency:
    @pytest.fixture(autouse=True)
    def initialize_settings(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    def test_dependency_add_locks_filesystem_source(self, tmp_path: Path, mocker: MockerFixture) -> None:
        cache = tmp_path / "cache"
        discover = mocker.patch("vcsinstall.cache.paths.user_cache_path", return_value=cache)
        target = tmp_path / "custom.toml"
        target.write_text("version = 1\n")
        lock = tmp_path / "vcsinstall.lock"
        lock.write_text("version = 1\n")
        source = tmp_path / "source"
        source.mkdir()
        (source / "file").write_bytes(b"literal\x00content\n")
        for options, outcome in (([], "added"), (["--redefine"], "unchanged")):
            result = CliRunner().invoke(
                application.app,
                ["-p", "automation", "--config", str(target), "dependency", "add", *options, "ci", source.as_uri()],
            )

            assert result.exit_code == 0, result.output
            record = json.loads(result.stdout)
            assert record["outcome"] == outcome
            assert record["vcs"] == "filesystem"
            assert record["repository"] == str(source)
            assert record["ref_kind"] == "default"
            assert record["ref"] is None
            assert record["revision"].startswith("sha256:")
            assert record["content"] is None
            assert not project_config.is_set()
            assert not active_workspace.is_set()
            assert not active_cache_root.is_set()
        config = tomllib.loads(target.read_text())
        locked = tomllib.loads(lock.read_text())
        assert config["dependencies"]["ci"] == {"vcs": "filesystem", "repository": str(source)}
        assert locked["dependencies"]["ci"]["revision"] == record["revision"]
        assert not config.get("entries")
        assert discover.call_count == 2
        assert not cache.exists()

    @pytest.mark.parametrize("fail", [False, True])
    def test_cache_root_scope_is_active_during_operation_and_restored_afterward(
        self, tmp_path: Path, mocker: MockerFixture, fail: bool
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        invocation = tmp_path / "invocation" / "nested"
        invocation.mkdir(parents=True)
        mocker.patch.object(dependency.Path, "cwd", return_value=invocation)
        expected = CacheRootPath(invocation.parent / "selected-cache")
        outer = CacheRootPath(tmp_path / "outer-cache")
        observed = []

        def operation(
            name: DependencyName,
            prepared: Dependency,
            *,
            source: SourceAdapter,
            hosting: HostingAdapter,
            redefine: bool,
        ) -> Result[None]:
            observed.append(active_cache_root.get())
            if fail:
                raise RuntimeError("operation failed")
            return Ok(None)

        mocker.patch.object(dependency, "dependency_add", side_effect=operation)

        with active_cache_root.scope(outer):
            result = CliRunner().invoke(
                application.app,
                [
                    "--config",
                    str(config),
                    "--cache-dir",
                    "../selected-cache",
                    "dependency",
                    "add",
                    "--vcs",
                    "git",
                    "ci",
                    "unused-source",
                ],
            )

            assert observed == [expected]
            assert active_cache_root.get() == outer
        assert not active_cache_root.is_set()
        assert not expected.exists()
        if fail:
            assert isinstance(result.exception, RuntimeError)
        else:
            assert result.exit_code == 0, result.output

    def test_configuration_change_is_reported_when_the_prepared_lock_and_entries_are_unchanged(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide.txt").write_bytes(b"original")
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        declarations = tomllib.loads(config.read_text())
        declarations["dependencies"]["ci"]["repository"] = "source"
        config.write_text(tomli_w.dumps(declarations))
        lock = config.with_name("vcsinstall.lock")
        lock.write_bytes(b"# Preserve unchanged lock state.\n" + lock.read_bytes())
        before = lock.read_bytes()

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 0, output
        assert [record["outcome"] for record in records] == ["replaced", "unchanged"]
        assert tomllib.loads(config.read_text())["dependencies"]["ci"]["repository"] == str(source)
        assert lock.read_bytes() == before
        assert (tmp_path / "installed").read_bytes() == b"original"

    @pytest.mark.parametrize("selector,value", [("branch", "Main+%2F"), ("tag", "Release+%2F"), ("rev", "abc123")])
    def test_selector_options_and_fragments_produce_equivalent_requests(
        self, tmp_path: Path, mocker: MockerFixture, selector: str, value: str
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        operation = mocker.patch.object(dependency, "dependency_add", return_value=Ok(None))
        repository = "git+https://host/repository"
        requests = []
        for locator, options in (
            (repository, ["--" + selector, value]),
            (repository + "#" + selector + "=" + quote(value, safe=""), []),
        ):
            result = CliRunner().invoke(
                application.app,
                ["--config", str(config), "dependency", "add", *options, "ci", locator],
            )

            assert result.exit_code == 0, result.output
            request = operation.call_args.args[1]
            assert request.repository == "https://host/repository"
            assert request.vcs == "git"
            assert request.selector is not None
            assert request.selector.kind.value == ("revision" if selector == "rev" else selector)
            assert request.selector.value == value
            requests.append(request)

        assert requests[0] == requests[1]

    @pytest.mark.parametrize(
        "repository,options",
        [
            ("https://host/repository", ["--branch", "main", "--tag", "v1"]),
            ("https://host/repository#branch=main", ["--branch", "main"]),
            ("https://host/repository#tag=v1", ["--rev", "abc123"]),
            ("https://host/repository#main", ["--tag", "v1"]),
            ("https://host/repository#branch=x&tag=y", []),
        ],
    )
    def test_selector_conflicts_and_invalid_fragments_fail_before_loading_or_runtime(
        self, tmp_path: Path, mocker: MockerFixture, repository: str, options: list[str]
    ) -> None:
        operation = mocker.patch.object(dependency, "dependency_add", return_value=Ok(None))
        load = mocker.spy(dependency, "load_workspace")
        cache = mocker.spy(dependency, "resolve_cache_root")

        result = CliRunner().invoke(
            application.app,
            [
                "-p",
                "automation",
                "--config",
                str(tmp_path / "missing"),
                "dependency",
                "add",
                *options,
                "ci",
                repository,
            ],
        )

        assert result.exit_code == 1, result.output
        operation.assert_not_called()
        load.assert_not_called()
        cache.assert_not_called()

    def test_selector_option_preserves_literal_hash_in_path(self, tmp_path: Path, mocker: MockerFixture) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        operation = mocker.patch.object(dependency, "dependency_add", return_value=Ok(None))
        mocker.patch.object(dependency.Path, "cwd", return_value=tmp_path)

        result = CliRunner().invoke(
            application.app,
            ["--config", str(config), "dependency", "add", "--vcs", "git", "--branch", "main", "ci", "repo#literal"],
        )

        assert result.exit_code == 0, result.output
        request = operation.call_args.args[1]
        assert request.repository == str(tmp_path / "repo#literal")
        assert request.selector is not None
        assert request.selector.kind.value == "branch"
        assert request.selector.value == "main"

    def test_prepares_relative_repository_against_invocation_directory(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "vcsinstall.toml"
        config.write_text("version = 1\n")
        invocation = tmp_path / "elsewhere" / "nested"
        invocation.mkdir(parents=True)
        mocker.patch.object(dependency.Path, "cwd", return_value=invocation)
        operation = mocker.patch.object(dependency, "dependency_add", return_value=Ok(None))

        result = CliRunner().invoke(
            application.app,
            ["--config", str(config), "dependency", "add", "--vcs", "git", "ci", "../shared"],
        )

        assert result.exit_code == 0, result.output
        prepared = operation.call_args.args[1]
        assert prepared.vcs == VcsId.git
        assert prepared.repository == str(invocation.parent / "shared")

    @pytest.mark.parametrize("filesystem", [False, True])
    def test_cache_resolution_failure_prevents_project_changes(
        self, tmp_path: Path, mocker: MockerFixture, filesystem: bool
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")
        source = tmp_path / "source"
        source.mkdir()
        repository = source.as_uri() if filesystem else str(source)
        mocker.patch("vcsinstall.cache.paths.user_cache_path", side_effect=OSError("unavailable"))
        mocker.patch("pathlib.Path.home", side_effect=RuntimeError("unavailable"))

        result = CliRunner().invoke(
            application.app,
            ["-p", "automation", "--config", str(config), "dependency", "add", "ci", repository],
        )

        assert result.exit_code == 3, result.output
        record = json.loads(result.stdout)
        assert record["code"] == cache_errors.AccessFailed.model_fields["code"].default
        assert "--cache-dir" in record["content"]
        assert config.read_text() == "version = 1\n"
        assert not config.with_name("vcsinstall.lock").exists()
        assert not project_config.is_set()
        assert not active_workspace.is_set()

    def test_dependency_add_rejects_production_noop_source(self, tmp_path: Path) -> None:
        config = tmp_path / "custom.toml"
        declaration = (
            '[dependencies.ci]\nvcs = "noop"\nrepository = "fixture-id"\n'
            'selector = { kind = "branch", value = "fixture branch" }\n'
        )
        config_text = "version = 1\n" + declaration
        lock_text = config_text + 'revision = "opaque-revision"\n'
        config.write_text(config_text)
        lock = tmp_path / "vcsinstall.lock"
        lock.write_text(lock_text)

        result = CliRunner().invoke(
            application.app,
            ["-p", "automation", "--config", str(config), "dependency", "add", "--vcs", "noop", "other", "fixture-id"],
        )

        assert result.exit_code == 1, result.output
        assert "test" in json.loads(result.stdout)["content"]
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert config.read_text() == config_text
        assert lock.read_text() == lock_text

    def test_runtime_exception_cleans_up_workspace(self, tmp_path: Path, mocker: MockerFixture) -> None:
        target = tmp_path / "custom.toml"
        target.write_text("version = 1\n")
        mocker.patch.object(dependency, "dependency_add", side_effect=RuntimeError("operation failed"))

        result = CliRunner().invoke(
            application.app, ["--config", str(target), "dependency", "add", "--vcs", "git", "ci", "unused-source"]
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

    @pytest.mark.parametrize(
        "arguments,invalid_parameter",
        [
            (["Bad/Name", "source"], "Bad/Name"),
            (["--branch", "", "ci", "source"], "--branch"),
            (["--tag", "", "ci", "source"], "--tag"),
            (["--rev", "", "ci", "source"], "--rev"),
        ],
    )
    def test_dependency_add_rejects_invalid_values_before_loading(
        self, tmp_path: Path, arguments: list[str], invalid_parameter: str
    ) -> None:
        result = CliRunner().invoke(
            application.app,
            ["-p", "automation", "--config", str(tmp_path / "missing"), "dependency", "add", *arguments],
        )

        assert result.exit_code == 2
        assert invalid_parameter in result.output
        assert not project_config.is_set()
        assert not active_workspace.is_set()

    def test_help_describes_available_command(self) -> None:
        result = CliRunner().invoke(application.app, ["dependency", "add", "--help"])

        assert result.exit_code == 0
        assert "lock" in result.stdout
        for option in ("--vcs", "--branch", "--tag", "--rev", "--redefine"):
            assert option in result.stdout

    @pytest.mark.parametrize("filename", ["custom.toml", "vcsinstall.lock"])
    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    @pytest.mark.parametrize(
        "vcs,source_problem",
        [
            ("git", source_errors.InvalidGitReference(field="branch", value=SelectorValue("bad..branch"))),
            ("git", source_errors.InvalidGitRevision(field="rev", value="HEAD")),
            ("filesystem", source_errors.InvalidFilesystemRevision(field="rev", value="bad-fingerprint")),
        ],
    )
    def test_adapter_diagnostics_reach_cli(
        self,
        tmp_path: Path,
        filename: str,
        protocol: str,
        vcs: str,
        source_problem: source_errors.InvalidGitReference | source_errors.InvalidRevision,
    ) -> None:
        config = tmp_path / "custom.toml"
        config.write_text("version = 1\n")
        kind = "revision" if source_problem.field == "rev" else source_problem.field
        text = (
            f'version = 1\n[dependencies.ci]\nvcs = "{vcs}"\nrepository = "unused-source"\n'
            f'selector = {{ kind = "{kind}", value = "{source_problem.value}" }}\n'
        )
        if filename.endswith("lock"):
            text += 'revision = "' + "1" * 40 + '"\n'
        (tmp_path / filename).write_text(text)

        result = CliRunner().invoke(
            application.app, ["-p", protocol, "--config", str(config), "dependency", "add", "ci", "unused-source"]
        )

        assert result.exit_code == 2
        assert not result.stderr
        content = json.loads(result.stdout)["content"] if protocol == "automation" else result.stdout
        for fix in source_problem.ways_to_fix:
            assert fix.format(error=source_problem) in content
        assert "dependencies.ci" in content
        assert str(tmp_path / filename) in content
        if protocol != "automation":
            return
        record = json.loads(result.stdout)
        assert record["code"] == workspace_errors.DependencyValidation.model_fields["code"].default
        assert record["path"] == str(tmp_path / filename)
        assert record["dependency"] == "ci"
        assert record["source_code"] == source_problem.code
        assert record["source_field"] == source_problem.field
        assert record["source_value"] == source_problem.value
        if isinstance(source_problem, source_errors.InvalidRevision):
            assert record["source_vcs"] == vcs

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        path = tmp_path / "vcsinstall.toml"
        path.write_text("# project comment\nversion = 1\n")
        return path

    @pytest.mark.parametrize("layout", ["git", "bare", "linked", "plain", "nested", "force_fs", "file_url"])
    def test_local_detection_and_explicit_overrides(  # noqa: CCR001 — Prepare each supported native repository layout.
        self, config: Path, tmp_path: Path, layout: str
    ) -> None:
        source = tmp_path / "source"
        if layout == "plain":
            source.mkdir()
            (source / "guide.txt").write_bytes(b"live files")
        else:
            client = create_repository(source)
            if layout == "bare":
                source = tmp_path / "bare"
                clone_mirror(client.repository, source).unwrap()
            elif layout == "linked":
                source = tmp_path / "linked"
                client.run(["worktree", "add", "-b", "linked", "--", str(source)]).unwrap()
            elif layout == "nested":
                source = source / "nested"
                source.mkdir()
                (source / "guide.txt").write_bytes(b"selected directory")
        options = ["--vcs", "filesystem"] if layout == "force_fs" else []
        locator = source.as_uri() if layout == "file_url" else str(source)
        code, records, output = invoke_add(config, tmp_path / "cache", *options, "ci", locator)

        assert code == 0, output
        expected = "git" if layout in ("git", "bare", "linked") else "filesystem"
        assert records[0]["vcs"] == expected
        assert tomllib.loads(config.read_text())["dependencies"]["ci"]["vcs"] == expected
        assert (tmp_path / "cache").exists() == (expected == "git")

    def test_corrupt_git_metadata_does_not_fall_back_to_filesystem(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        (source / ".git").mkdir(parents=True)
        before = config.read_bytes()

        code, records, output = invoke_add(config, tmp_path / "cache", "ci", str(source))

        assert code == 3, output
        assert records[0]["code"] == "vcs_command_failed"
        assert config.read_bytes() == before
        assert not config.with_name("vcsinstall.lock").exists()
        assert not (tmp_path / "cache").exists()

    @pytest.mark.parametrize("kind,value", [("rev", "HEAD~1"), ("branch", "bad..branch"), ("tag", "bad tag")])
    def test_invalid_git_selection_fails_before_acquisition(
        self, config: Path, tmp_path: Path, kind: str, value: str
    ) -> None:
        cache = tmp_path / "cache"
        before = config.read_bytes()

        code, _, output = invoke_add(
            config, cache, "--vcs", "git", "--" + kind, value, "ci", str(tmp_path / "missing-source")
        )

        assert code == ExitCode.invalid_arguments, output
        assert config.read_bytes() == before
        assert not config.with_name("vcsinstall.lock").exists()
        assert not cache.exists()

    def test_plain_directory_detection_does_not_require_git(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide").write_bytes(b"live files")
        native = mocker.patch("vcsinstall.vcs_clients.git.subprocess.run", side_effect=FileNotFoundError("git"))

        code, records, output = invoke_add(config, tmp_path / "cache", "ci", str(source))

        assert code == 0, output
        assert records[0]["vcs"] == "filesystem"
        native.assert_not_called()

    def test_normalization_rejection_allows_the_next_backend(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide").write_bytes(b"filesystem source")
        mocker.patch("vcsinstall.sources.adapters.git.GitSourceAdapter.normalize_repository", return_value=Ok(None))

        code, records, output = invoke_add(config, tmp_path / "cache", "ci", str(source))

        assert code == 0, output
        assert records[0]["vcs"] == "filesystem"
        assert not (tmp_path / "cache").exists()

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("selector", ["default", "branch", "tag", "annotated", "revision", "bare"])
    def test_git_selector_forms(self, config: Path, tmp_path: Path, object_format: str, selector: str) -> None:
        source = tmp_path / "source"
        client = create_repository(source, object_format=object_format)
        revision = client.resolve_commit("HEAD").unwrap()
        client.create_tag("release").unwrap()
        client.create_tag("annotated", message="Release").unwrap()
        fragments = {
            "default": "",
            "branch": "#branch=trunk",
            "tag": "#tag=release",
            "annotated": "#tag=annotated",
            "revision": "#rev=" + revision[:12],
            "bare": "#trunk",
        }
        code, records, output = invoke_add(
            config, tmp_path / "cache", "ci", "git+" + source.as_uri() + fragments[selector]
        )
        assert code == 0, output
        record = records[0]
        assert record["revision"] == revision
        assert record["ref_kind"] == (
            "revision" if selector == "revision" else "tag" if selector in ("tag", "annotated") else "branch"
        )
        declared = tomllib.loads(config.read_text())["dependencies"]["ci"]
        assert declared["repository"] == str(source)
        assert declared["vcs"] == "git"
        assert declared["selector"] == {"kind": record["ref_kind"], "value": record["ref"]}
        if selector == "revision":
            assert declared["selector"]["value"] == revision
        assert not list(source.glob(".vcsinstall*"))

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("reference_kind", ["branch", "tag"])
    def test_revision_pin_ignores_hexadecimal_reference(
        self, config: Path, tmp_path: Path, object_format: str, reference_kind: str
    ) -> None:
        client = create_repository(tmp_path / "source", object_format=object_format)
        previous = client.resolve_commit("HEAD").unwrap()
        (client.path / "guide.txt").write_bytes(b"newer content")
        client.commit("Advance source", all_tracked=True).unwrap()
        value = previous[:12]
        if reference_kind == "branch":
            client.create_branch(value).unwrap()
        else:
            client.create_tag(value).unwrap()

        code, records, output = invoke_add(config, tmp_path / "cache", "--rev", value, "ci", str(client.path))

        assert code == 0, output
        assert records[0]["revision"] == previous
        for path in (config, config.with_name("vcsinstall.lock")):
            dependency = tomllib.loads(path.read_text())["dependencies"]["ci"]
            assert dependency["selector"] == {"kind": "revision", "value": previous}

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    def test_new_abbreviated_revision_is_acquired_from_populated_cache(
        self, config: Path, tmp_path: Path, object_format: str
    ) -> None:
        client = create_repository(tmp_path / "source", object_format=object_format)
        previous = client.resolve_commit("HEAD").unwrap()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
        (client.path / "guide.txt").write_bytes(b"newer content")
        revision = client.commit("Advance source", all_tracked=True).unwrap()

        code, records, output = invoke_add(config, cache, "--rev", revision[:12], "pinned", str(client.path))

        assert code == 0, output
        assert records[0]["revision"] == revision
        for path in (config, config.with_name("vcsinstall.lock")):
            dependency = tomllib.loads(path.read_text())["dependencies"]["pinned"]
            assert dependency["selector"] == {"kind": "revision", "value": revision}
        locked = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["dependencies"]
        assert locked["ci"]["revision"] == previous

    def test_missing_abbreviated_revision_fetch_failure_preserves_project(self, config: Path, tmp_path: Path) -> None:
        client = create_repository(tmp_path / "source")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed", files=b"original\n")
        installed = tmp_path / "installed"
        installed.write_bytes(b"original\n")
        (client.path / "guide.txt").write_bytes(b"newer content")
        revision = client.commit("Advance source", all_tracked=True).unwrap()
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes(), installed.read_bytes())
        client.path.rename(tmp_path / "unavailable-source")

        code, records, output = invoke_add(
            config, cache, "--vcs", "git", "--rev", revision[:12], "--redefine", "ci", str(client.path)
        )

        assert code == ExitCode.environment_error, output
        assert records[0]["code"] == "vcs_command_failed"
        assert (
            config.read_bytes(),
            config.with_name("vcsinstall.lock").read_bytes(),
            installed.read_bytes(),
        ) == before

    @pytest.mark.parametrize("kind", ["branch", "tag", "rev"])
    @pytest.mark.parametrize("form", ["option", "fragment"])
    def test_selector_whitespace_is_stripped(self, config: Path, tmp_path: Path, kind: str, form: str) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        revision = client.resolve_commit("HEAD").unwrap()
        client.create_tag("release").unwrap()
        value = {"branch": "trunk", "tag": "release", "rev": revision[:12]}[kind]
        locator = "git+" + source.as_uri()
        arguments = (
            ["--" + kind, f" \t{value}\n", "ci", locator]
            if form == "option"
            else ["ci", locator + f"#{kind}=%20%09{value}%0A"]
        )

        code, records, output = invoke_add(config, tmp_path / "cache", *arguments)

        assert code == 0, output
        expected = revision if kind == "rev" else value
        assert records[0]["ref"] == expected
        assert records[0]["revision"] == revision
        for path in (config, config.with_name("vcsinstall.lock")):
            selector = tomllib.loads(path.read_text())["dependencies"]["ci"]["selector"]
            assert selector == {"kind": "revision" if kind == "rev" else kind, "value": expected}

    @pytest.mark.parametrize("kind", ["branch", "tag", "rev"])
    def test_whitespace_only_selector_option_is_rejected(self, config: Path, tmp_path: Path, kind: str) -> None:
        cache = tmp_path / "cache"
        before = config.read_bytes()

        code, _, output = invoke_add(config, cache, "--" + kind, " \t\n", "ci", "git+https://example.com/repo")

        assert code == 2, output
        assert config.read_bytes() == before
        assert not config.with_name("vcsinstall.lock").exists()
        assert not cache.exists()

    def test_default_replacement_refreshes_and_reinfers(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        first = client.resolve_commit("HEAD").unwrap()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(source))[0] == 0
        client.create_branch("stable", checkout=True).unwrap()
        (source / "guide.txt").write_text("new\n")
        client.commit("New default", all_tracked=True).unwrap()
        current = client.resolve_commit("HEAD").unwrap()
        code, records, output = invoke_add(config, cache, "--redefine", "ci", str(source))
        assert code == 0, output
        assert records[0]["ref"] == "stable"
        assert records[0]["revision"] == current
        assert records[0]["previous_revision"] == first
        previous_bytes = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        assert invoke_add(config, cache, "--redefine", "ci", str(source))[1][0]["outcome"] == "unchanged"
        assert previous_bytes == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

    def test_unavailable_default_does_not_reuse_cached_head(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(source))[0] == 0
        lock = config.with_name("vcsinstall.lock")
        before = (config.read_bytes(), lock.read_bytes())
        client.run(["checkout", "--detach", client.resolve_commit("HEAD").unwrap(), "--"]).unwrap()

        code, records, output = invoke_add(config, cache, "--redefine", "ci", str(source))

        assert code == 3, output
        assert "default branch" in str(records[0]["content"])
        assert (config.read_bytes(), lock.read_bytes()) == before
        code, records, output = invoke_add(
            config, cache, "--redefine", "ci", "git+" + source.as_uri() + "#branch=trunk"
        )
        assert code == 0, output
        assert records[0]["ref"] == "trunk"

    def test_duplicate_and_option_conflicts_do_not_access_cache(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        cache = tmp_path / "unused-cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        before = config.read_bytes()
        code, records, _ = invoke_add(config, cache, "ci", source.as_uri())
        assert code == 1
        assert "--redefine" in str(records[0]["content"])
        assert config.read_bytes() == before
        assert not cache.exists()

    @pytest.mark.parametrize(
        "repository,options",
        [
            ("https://example.com/shared", ["--vcs", "filesystem"]),
            ("svn+https://example.com/shared", []),
            ("git+https://example.com/shared#", []),
            ("git+https://example.com/shared#branch=main&tag=v1", []),
            ("git+https://example.com/shared#branch=main", ["--tag", "v1"]),
            ("git+https://example.com/shared#branch=%20", []),
            ("git+https://example.com/shared#%20", []),
            ("git+https://example.com/shared", ["--vcs", "filesystem"]),
            ("file:///missing", ["--branch", "main"]),
            ("/missing", ["--branch", "main", "--tag", "v1"]),
        ],
    )
    def test_invalid_inputs_preserve_project(
        self, config: Path, tmp_path: Path, repository: str, options: list[str]
    ) -> None:
        before = config.read_bytes()
        cache = tmp_path / "cache"
        code, records, output = invoke_add(config, cache, *options, "ci", repository)
        assert code == 1, output
        assert records[0]["type"] == "error"
        assert config.read_bytes() == before
        assert not config.with_name("vcsinstall.lock").exists()
        assert not cache.exists()

    def test_literal_percent_in_selector(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        client.create_branch("literal%").unwrap()

        code, records, output = invoke_add(
            config, tmp_path / "cache", "ci", "git+" + source.as_uri() + "#branch=literal%"
        )

        assert code == 0, output
        assert records[0]["ref"] == "literal%"
        assert tomllib.loads(config.read_text())["dependencies"]["ci"]["selector"] == {
            "kind": "branch",
            "value": "literal%",
        }

    def test_ambiguous_and_deleted_refs_fail_without_mutation(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        client.create_tag("trunk").unwrap()
        before = config.read_bytes()
        code, records, _ = invoke_add(config, tmp_path / "cache", "ci", "git+" + source.as_uri() + "#trunk")
        assert code == 1
        assert "ambiguous" in str(records[0]["content"])
        assert config.read_bytes() == before
        assert invoke_add(config, tmp_path / "cache", "ci", "git+" + source.as_uri() + "#tag=trunk")[0] == 0
        previous_bytes = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        client.delete_tag("trunk").unwrap()
        code, _, _ = invoke_add(
            config, tmp_path / "cache", "--redefine", "ci", "git+" + source.as_uri() + "#tag=trunk"
        )
        assert code == 3
        assert previous_bytes == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

    @pytest.mark.parametrize("local", ["original\n", "selected\n", "missing", "conflict\n"])
    def test_replacement_protects_and_refreshes_entries(self, config: Path, tmp_path: Path, local: str) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide.txt").write_text("original\n")
        assert invoke_add(config, tmp_path / "unused", "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/guide", files=b"original\n")
        lock = config.with_name("vcsinstall.lock")
        target = tmp_path / "guide"
        if local != "missing":
            target.write_text(local)
        (source / "guide.txt").write_text("selected\n")
        before = (config.read_bytes(), lock.read_bytes())
        code, records, output = invoke_add(config, tmp_path / "unused", "--redefine", "ci", source.as_uri())
        if local == "conflict\n":
            assert code == 3, output
            assert before == (config.read_bytes(), lock.read_bytes())
            assert target.read_text() == local
        else:
            assert code == 0, output
            assert [record["type"] for record in records] == ["dependency", "installation"]
            assert records[1]["status"] == "unmodified"
            assert target.read_text() == "selected\n"
            assert tomllib.loads(lock.read_text())["entries"][0]["hash"] == hashes.hash_file(b"selected\n")

    def test_relative_repository_uses_invocation_directory(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        nested = tmp_path / "nested"
        nested.mkdir()
        source = nested / "source"
        create_repository(source)
        mocker.patch("pathlib.Path.cwd", return_value=nested)
        code, records, output = invoke_add(config, tmp_path / "cache", "ci", "source")
        assert code == 0, output
        assert records[0]["repository"] == str(source)

    @pytest.mark.parametrize("local_change", ["modified", "added"])
    def test_directory_refresh_protects_all_local_files(self, config: Path, tmp_path: Path, local_change: str) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "a").write_bytes(b"old a")
        (source / "b").write_bytes(b"old b")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files={"a": b"old a", "b": b"old b"})
        installed = tmp_path / "installed"
        installed.mkdir()
        local_a = b"local a" if local_change == "modified" else b"old a"
        (installed / "a").write_bytes(local_a)
        if local_change == "added":
            (installed / "addition").write_bytes(b"keep")
        (source / "a").write_bytes(b"new a")
        (source / "b").unlink()
        (source / "new").write_bytes(b"added")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        code, _, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 3, output
        assert before == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

        assert (installed / "a").read_bytes() == local_a
        assert not (installed / "new").exists()
        if local_change == "added":
            assert (installed / "addition").read_bytes() == b"keep"

    @pytest.mark.parametrize("vcs", ["filesystem", "git"])
    def test_replacement_applies_each_entry_source_and_destination(
        self, config: Path, tmp_path: Path, vcs: str
    ) -> None:
        source = tmp_path / "source"
        client = create_repository(source) if vcs == "git" else None
        if client is None:
            source.mkdir()
            (source / "guide.txt").write_bytes(b"original\n")
        (source / "bundle" / "nested").mkdir(parents=True)
        (source / "bundle" / "nested" / "guide").write_bytes(b"bundle old")
        if client is not None:
            client.add(".").unwrap()
            client.commit("Initial bundle").unwrap()
        locator = "git+" + source.as_uri() if client is not None else source.as_uri()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", locator)[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed-file", files=b"original\n")
        declare_entry(config, source="@/bundle", destination="@/installed-dir", files={"nested/guide": b"bundle old"})
        (tmp_path / "installed-file").write_bytes(b"original\n")
        installed = tmp_path / "installed-dir"
        (installed / "nested").mkdir(parents=True)
        (installed / "nested" / "guide").write_bytes(b"bundle old")
        (source / "guide.txt").write_bytes(b"new standalone")
        (source / "bundle" / "nested" / "guide").write_bytes(b"new nested")
        (source / "bundle" / "nested" / "added").write_bytes(b"added nested")
        if client is not None:
            client.add(".").unwrap()
            client.commit("Update both entries").unwrap()

        code, records, output = invoke_add(config, cache, "--redefine", "ci", locator)

        assert code == 0, output
        assert [record["type"] for record in records] == ["dependency", "installation", "installation"]
        assert (tmp_path / "installed-file").read_bytes() == b"new standalone"
        assert (installed / "nested" / "guide").read_bytes() == b"new nested"
        assert (installed / "nested" / "added").read_bytes() == b"added nested"
        baselines = {
            entry["destination"]: entry
            for entry in tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"]
        }
        assert baselines["@/installed-file"]["source_path"] == "@/guide.txt"
        assert baselines["@/installed-dir"]["source_path"] == "@/bundle"

    @pytest.mark.parametrize(
        "change,error_type",
        [
            ("removed", workspace_errors.OrphanedBaseline),
            ("destination", workspace_errors.OrphanedBaseline),
            ("entry_owner", workspace_errors.OrphanedBaseline),
            ("baseline_owner", workspace_errors.MissingBaseline),
        ],
    )
    def test_redefinition_rejects_removed_or_reassigned_baselines(
        self, config: Path, tmp_path: Path, change: str, error_type: type[core_errors.EnvironmentError]
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide.txt").write_bytes(b"original")
        cache = tmp_path / "cache"
        for name in ("ci", "formatting"):
            assert invoke_add(config, cache, name, source.as_uri())[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed", files=b"original")
        installed = tmp_path / "installed"
        installed.write_bytes(b"original")
        lock = config.with_name("vcsinstall.lock")
        path = lock if change == "baseline_owner" else config
        data = tomllib.loads(path.read_text())
        if change == "removed":
            data["entries"] = []
        elif change == "destination":
            data["entries"][0]["destination"] = "@/moved"
        else:
            data["entries"][0]["dependency"] = "formatting"
        path.write_text(tomli_w.dumps(data))
        before = (config.read_bytes(), lock.read_bytes())

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == ExitCode.environment_error, output
        assert records[0]["code"] == error_type.model_fields["code"].default
        assert records[0]["dependency"] == "ci"
        assert records[0]["destination"] == "@/installed"
        assert records[0]["path"] == str(lock)
        for guidance in error_type.model_fields["ways_to_fix"].default:
            assert guidance in str(records[0]["content"])
        assert before == (config.read_bytes(), lock.read_bytes())
        assert installed.read_bytes() == b"original"

    def test_whole_selection_is_checked_before_writes(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        for name in ("a", "b"):
            (source / name).write_bytes(b"old")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0
        for name in ("a", "b"):
            declare_entry(config, source="@/" + name, destination="@/" + name, files=b"old")
            (tmp_path / name).write_bytes(b"old" if name == "a" else b"local edit")
            (source / name).write_bytes(b"new")
        before = config.with_name("vcsinstall.lock").read_bytes()
        code, _, output = invoke_add(config, tmp_path / "cache", "--redefine", "ci", source.as_uri())
        assert code == 3, output
        assert (tmp_path / "a").read_bytes() == b"old"
        assert (tmp_path / "b").read_bytes() == b"local edit"
        assert config.with_name("vcsinstall.lock").read_bytes() == before

    @pytest.mark.parametrize("addition", [False, True])
    def test_directory_to_file_protects_local_additions(self, config: Path, tmp_path: Path, addition: bool) -> None:
        source = tmp_path / "source"
        source.mkdir()
        selected = source / "selected"
        selected.mkdir()
        (selected / "old").write_bytes(b"old")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/selected", destination="@/installed", files={"old": b"old"})
        installed = tmp_path / "installed"
        installed.mkdir()
        (installed / "old").write_bytes(b"old")
        if addition:
            (installed / "keep").write_bytes(b"keep")
        (selected / "old").unlink()
        selected.rmdir()
        selected.write_bytes(b"replacement")
        code, _, output = invoke_add(config, tmp_path / "cache", "--redefine", "ci", source.as_uri())
        if addition:
            assert code == 3, output
            assert (installed / "old").read_bytes() == b"old"
            assert (installed / "keep").read_bytes() == b"keep"
        else:
            assert code == 0, output
            assert installed.read_bytes() == b"replacement"

    @pytest.mark.parametrize("backend", ["filesystem", "git"])
    @pytest.mark.parametrize("nested", [False, True])
    @pytest.mark.parametrize("obstruction", ["empty", "empty_descendants", "local_file"])
    def test_selected_file_replaces_directory_obstructions(  # noqa: CCR001 — Backend, mapping and obstruction cases.
        self, config: Path, tmp_path: Path, backend: str, nested: bool, obstruction: str
    ) -> None:
        source = tmp_path / "source"
        client = create_repository(source) if backend == "git" else None
        source.mkdir(exist_ok=True)
        selection = source / "bundle" if nested else source
        selection.mkdir(exist_ok=True)
        selected = selection / "guide.txt"
        selected.write_bytes(b"original\n")
        if client is not None and nested:
            client.add(".").unwrap()
            client.commit("Initial mapping", all_tracked=True).unwrap()
        locator = str(source) if backend == "git" else source.as_uri()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", locator)[0] == 0
        declare_entry(
            config,
            source="@/bundle" if nested else "@/guide.txt",
            destination="@/installed",
            files={"guide.txt": b"original\n"} if nested else b"original\n",
        )
        installed = tmp_path / "installed"
        target = installed / "guide.txt" if nested else installed
        target.mkdir(parents=True)
        if obstruction == "empty_descendants":
            (target / "empty" / "nested").mkdir(parents=True)
        elif obstruction == "local_file":
            (target / "keep").write_bytes(b"local addition")
        selected.write_bytes(b"selected\n")
        if client is not None:
            client.commit("Update mapping", all_tracked=True).unwrap()
        lock_path = config.with_name("vcsinstall.lock")
        before = (config.read_bytes(), lock_path.read_bytes())

        code, _, output = invoke_add(config, cache, "--redefine", "ci", locator)

        if obstruction == "local_file":
            assert code == 3, output
            assert (target / "keep").read_bytes() == b"local addition"
            assert before == (config.read_bytes(), lock_path.read_bytes())
        else:
            assert code == 0, output
            assert target.is_file()
            assert target.read_bytes() == b"selected\n"
            baseline = tomllib.loads(lock_path.read_text())["entries"][0]
            assert baseline["kind"] == ("directory" if nested else "file")
            recorded = baseline["files"][0] if nested else baseline
            assert recorded["hash"] == hashes.hash_file(b"selected\n")

    def test_redefinition_replaces_complete_changed_entry(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        files = {
            "keep.txt": b"keep",
            "old.txt": b"old",
            "sub/old.txt": b"nested old",
            "missing.txt": b"missing",
        }
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files=files)
        installed = tmp_path / "installed"
        for name in ("keep.txt", "old.txt", "sub/old.txt"):
            path = installed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
        os.utime(installed / "keep.txt", ns=(1, 1))
        (installed / "empty").mkdir()
        (tmp_path / "outside").write_bytes(b"keep outside")
        for name in ("old.txt", "sub/old.txt", "missing.txt"):
            (source / name).unlink()
        (source / "new.txt").write_bytes(b"new")

        code, _, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 0, output
        assert not (installed / "old.txt").exists()
        assert not (installed / "sub/old.txt").exists()
        assert not (installed / "missing.txt").exists()
        assert not (installed / "empty").exists()
        assert (installed / "keep.txt").read_bytes() == b"keep"
        assert (installed / "keep.txt").stat().st_mtime_ns != 1
        assert (tmp_path / "outside").read_bytes() == b"keep outside"
        assert (installed / "new.txt").read_bytes() == b"new"
        baseline = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"][0]
        assert {file["path"] for file in baseline["files"]} == {"@/keep.txt", "@/new.txt"}

    def test_upstream_deletion_records_expected_absence(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "selected").write_bytes(b"old")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/selected", destination="@/installed", files=b"old")
        (tmp_path / "installed").write_bytes(b"old")
        (source / "selected").unlink()
        code, _, output = invoke_add(config, tmp_path / "cache", "--redefine", "ci", source.as_uri())
        assert code == 0, output
        assert not (tmp_path / "installed").exists()
        baseline = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"][0]
        assert baseline["kind"] == "absent"
        assert "hash" not in baseline
        assert len(tomllib.loads(config.read_text())["entries"]) == 1

    @pytest.mark.parametrize(
        "initial",
        [
            "version = 1\ndependencies = {}\n",
            "version = 1\nentries = []\n",
            'version = 1\n[dependencies.other]\nvcs = "filesystem"\nrepository = "../other"\n'
            '[[entries]]\ndependency = "other"\nsource_path = "@/guide"\ndestination = "@/other"\n',
        ],
    )
    def test_serialization_preserves_existing_declarations(self, config: Path, tmp_path: Path, initial: str) -> None:
        config.write_text(initial)
        source = tmp_path / "source"
        source.mkdir()
        code, _, output = invoke_add(config, tmp_path / "cache", "ci", source.as_uri())
        assert code == 0, output
        written = tomllib.loads(config.read_text())
        original = tomllib.loads(initial)
        assert written["dependencies"]["ci"]["repository"] == str(source)
        for name, definition in original.get("dependencies", {}).items():
            assert written["dependencies"][name] == definition
        assert written.get("entries", []) == original.get("entries", [])

    @pytest.mark.parametrize("unsupported", [False, True])
    def test_git_replacement_uses_selected_committed_content(
        self, config: Path, tmp_path: Path, unsupported: bool
    ) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(source))[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed", files=b"original\n")
        installed = tmp_path / "installed"
        installed.write_bytes(b"original\n")
        if unsupported:
            (source / "guide.txt").unlink()
            (source / "guide.txt").symlink_to("outside")
        else:
            (source / "guide.txt").write_bytes(b"committed\x00bytes\r\n")
            (source / "outside").symlink_to("missing")
        client.add(".").unwrap()
        client.commit("Change source").unwrap()
        if not unsupported:
            (source / "guide.txt").write_bytes(b"uncommitted working tree\n")
        index = (source / ".git" / "index").read_bytes()
        previous = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        code, _, output = invoke_add(config, cache, "--redefine", "ci", str(source))
        assert (source / ".git" / "index").read_bytes() == index
        if unsupported:
            assert code == 3, output
            assert previous == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
            assert installed.read_bytes() == b"original\n"
        else:
            assert code == 0, output
            assert installed.read_bytes() == b"committed\x00bytes\r\n"
            assert (source / "guide.txt").read_bytes() == b"uncommitted working tree\n"

    @pytest.mark.parametrize("vcs", ["filesystem", "git"])
    def test_large_file_replacement_uses_bounded_python_memory(self, config: Path, tmp_path: Path, vcs: str) -> None:
        source = tmp_path / "source"
        client = create_repository(source) if vcs == "git" else None
        if client is None:
            source.mkdir()
            (source / "guide.txt").write_bytes(b"original\n")
        locator = "git+" + source.as_uri() if client is not None else source.as_uri()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", locator)[0] == 0
        declare_entry(config, source="@/guide.txt", destination="@/installed", files=b"original\n")
        installed = tmp_path / "installed"
        installed.write_bytes(b"original\n")
        size = 16 * 1024 * 1024
        with (source / "guide.txt").open("wb") as stream:
            stream.truncate(size)
        if client is not None:
            client.commit("Large file", all_tracked=True).unwrap()

        tracemalloc.start()
        try:
            code, _, output = invoke_add(config, cache, "--redefine", "ci", locator)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert code == 0, output
        assert peak < 6 * 1024 * 1024
        assert installed.stat().st_size == size
        with installed.open("rb") as destination, (source / "guide.txt").open("rb") as original:
            assert (
                hashlib.file_digest(destination, "sha256").digest() == hashlib.file_digest(original, "sha256").digest()
            )

    def test_literal_selector_encoding_and_option_equivalence(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        client.create_branch("Feature+%2F").unwrap()
        cache = tmp_path / "cache"
        code, records, output = invoke_add(config, cache, "ci", "git+" + source.as_uri() + "#branch=Feature+%252F")
        assert code == 0, output
        assert records[0]["ref"] == "Feature+%2F"
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        code, records, output = invoke_add(config, cache, "--redefine", "--branch", "Feature+%2F", "ci", str(source))
        assert code == 0, output
        assert records[0]["outcome"] == "unchanged"
        assert before == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

    def test_failed_direct_lockfile_write_reports_partial_state(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from vcsinstall.filesystem import errors as filesystem_errors

        source = tmp_path / "source"
        source.mkdir()
        lock = config.with_name("vcsinstall.lock")
        lock.write_text("# existing lock\nversion = 1\n")
        original_write = Path.write_bytes

        def write_file(path: Path, data: bytes) -> int:
            if path == lock:
                original_write(path, data[:4])
                raise OSError("simulated lockfile write failure")
            return original_write(path, data)

        mocker.patch("pathlib.Path.write_bytes", side_effect=write_file, autospec=True)
        code, records, output = invoke_add(config, tmp_path / "cache", "ci", source.as_uri())
        assert code == 3, output
        assert lock.read_bytes() == b"vers"
        assert "ci" in tomllib.loads(config.read_text())["dependencies"]
        assert records[0]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert records[0]["path"] == str(lock)
        assert str(config) in str(records[0]["reason"])
        assert not list(tmp_path.glob(".vcsinstall*"))

    def test_installation_write_failure_reports_inconsistency_and_original_error(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from llm_tool_cli.core.result import Err

        from vcsinstall.filesystem import FSClient
        from vcsinstall.filesystem import errors as filesystem_errors
        from vcsinstall.runtime import errors as runtime_errors

        source = tmp_path / "source"
        source.mkdir()
        (source / "old").write_bytes(b"old")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files={"old": b"old"})
        installed = tmp_path / "installed"
        installed.mkdir()
        (installed / "old").write_bytes(b"old")
        (source / "old").unlink()
        (source / "new").write_bytes(b"new")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        failure = filesystem_errors.AccessFailed(path=installed / "new", reason="simulated copy failure")
        mocker.patch.object(FSClient, "write_stream", return_value=Err([failure]))

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 3, output
        assert not (installed / "old").exists()
        assert before == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        notice = runtime_errors.InstallationWriteFailed()
        assert records[0]["code"] == notice.code
        assert notice.format_message() in str(records[0]["content"])
        assert records[1]["code"] == failure.code
        assert records[1]["path"] == str(installed / "new")
        content = str(records[1]["content"])
        assert failure.format_message() in content
        assert all(fix in content for fix in failure.ways_to_fix)

    def test_matching_installed_file_is_not_rewritten(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "selected").write_bytes(b"same")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/selected", destination="@/installed", files=b"same")
        installed = tmp_path / "installed"
        installed.write_bytes(b"same")
        timestamp = installed.stat().st_mtime_ns

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 0, output
        assert installed.stat().st_mtime_ns == timestamp
        assert records[0]["outcome"] == "unchanged"

    def test_matching_complete_directory_accepts_local_additions_without_rewriting(
        self, config: Path, tmp_path: Path
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "original").write_bytes(b"same")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files={"original": b"same"})
        installed = tmp_path / "installed"
        installed.mkdir()
        for name, data in (("original", b"same"), ("addition", b"added")):
            (source / name).write_bytes(data)
            (installed / name).write_bytes(data)
            os.utime(installed / name, ns=(1, 1))
        (installed / "empty").mkdir()

        code, _, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 0, output
        assert (installed / "empty").is_dir()
        assert all((installed / name).stat().st_mtime_ns == 1 for name in ("original", "addition"))
        baseline = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"][0]
        assert {file["path"] for file in baseline["files"]} == {"@/original", "@/addition"}

    def test_revision_only_change_does_not_recreate_installed_directory(self, config: Path, tmp_path: Path) -> None:
        source = tmp_path / "source"
        client = create_repository(source)
        locator = "git+" + source.as_uri()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", locator)[0] == 0
        declare_entry(config, source="@/", destination="@/installed", files={"guide.txt": b"original\n"})
        installed = tmp_path / "installed"
        installed.mkdir()
        (installed / "guide.txt").write_bytes(b"original\n")
        os.utime(installed / "guide.txt", ns=(1, 1))
        (installed / "empty").mkdir()
        lock_path = config.with_name("vcsinstall.lock")
        previous_revision = tomllib.loads(lock_path.read_text())["dependencies"]["ci"]["revision"]
        client.run(["commit", "--allow-empty", "-m", "Advance without content changes"]).unwrap()

        code, records, output = invoke_add(config, cache, "--redefine", "ci", locator)

        assert code == 0, output
        assert records[0]["revision"] != previous_revision
        assert tomllib.loads(lock_path.read_text())["dependencies"]["ci"]["revision"] == records[0]["revision"]
        assert (installed / "empty").is_dir()
        assert (installed / "guide.txt").stat().st_mtime_ns == 1

    def test_installation_failure_keeps_partial_content_and_prior_project_records(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from collections.abc import Callable
        from typing import BinaryIO

        from llm_tool_cli.core.result import Err, Result
        from llm_tool_cli.paths import ProjectPathId

        from vcsinstall.filesystem import FSClient
        from vcsinstall.filesystem import errors as filesystem_errors
        from vcsinstall.runtime import errors as runtime_errors

        source = tmp_path / "source"
        source.mkdir()
        for name in ("a", "b"):
            (source / name).write_bytes(b"old")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        for name in ("a", "b"):
            declare_entry(config, source="@/" + name, destination="@/" + name, files=b"old")
            (tmp_path / name).write_bytes(b"old")
            (source / name).write_bytes(b"new")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        write_stream = FSClient.write_stream

        def fail_second(
            client: FSClient,
            path: ProjectPathId,
            write: Callable[[BinaryIO], Result[None]],
            *,
            executable: bool = False,
        ) -> Result[None]:
            if path == "@/b":
                return Err([filesystem_errors.AccessFailed(path=tmp_path / "b", reason="second write failed")])
            return write_stream(client, path, write, executable=executable)

        mocker.patch.object(FSClient, "write_stream", autospec=True, side_effect=fail_second)

        code, records, output = invoke_add(config, cache, "--redefine", "ci", source.as_uri())

        assert code == 3, output
        assert (tmp_path / "a").read_bytes() == b"new"
        assert not (tmp_path / "b").exists()
        assert records[0]["code"] == runtime_errors.InstallationWriteFailed.model_fields["code"].default
        assert records[1]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert records[1]["path"] == str(tmp_path / "b")
        assert before == (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())


@pytest.mark.usefixtures("isolated_settings")
class TestListDependencies:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "custom.toml"
        definition = {
            "vcs": "git",
            "repository": "https://example.test/shared.git",
            "selector": {"kind": "branch", "value": "main"},
        }
        config.write_text(tomli_w.dumps({"version": 1, "dependencies": dict.fromkeys(("z-unused", "ci"), definition)}))
        config.with_name("vcsinstall.lock").write_text(
            tomli_w.dumps(
                {
                    "version": 1,
                    "dependencies": {
                        name: {**definition, "revision": revision * 40}
                        for name, revision in (("z-unused", "2"), ("ci", "1"))
                    },
                }
            )
        )
        return config

    def test_reports_ordered_dependencies_and_entry_counts_without_effects(  # noqa: CCR001 — Mixed local states.
        self, config: Path, mocker: MockerFixture
    ) -> None:
        for destination, expected, actual in (
            ("unmodified", b"original", b"original"),
            ("modified", b"original", b"edited"),
            ("missing", b"original", None),
            ("directory", {"a": b"a", "nested/b": b"b"}, {"a": b"a", "nested/b": b"b"}),
            ("partial", {"a": b"a", "b": b"b"}, {"a": b"a"}),
            ("mixed", {"a": b"a", "b": b"b"}, {"a": b"edited"}),
            ("addition", {"a": b"a"}, {"a": b"a", ".extra": b"new"}),
        ):
            declare_entry(config, source="@/" + destination, destination="@/" + destination, files=expected)
            local = (
                {destination: actual}
                if isinstance(actual, bytes)
                else {destination + "/" + child: data for child, data in (actual or {}).items()}
            )
            for path, data in local.items():
                target = config.parent / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        declarations = tomllib.loads(config.read_text())
        declarations["entries"].append({"dependency": "ci", "source_path": "@/new", "destination": "@/unlocked"})
        config.write_text(tomli_w.dumps(declarations))
        before = {path: path.read_bytes() for path in config.parent.rglob("*") if path.is_file()}
        for name in (
            "vcsinstall.cli.commands.dependency.resolve_cache_root",
            "vcsinstall.cli.commands.dependency.get_hosting",
            "vcsinstall.cache.paths.user_cache_path",
            "vcsinstall.vcs_clients.GitClient.run",
            "vcsinstall.sources.adapters.git.GitSourceAdapter.resolve",
            "vcsinstall.sources.adapters.git.GitSourceAdapter.inspect",
        ):
            mocker.patch(name, side_effect=AssertionError("Unexpected source or cache access"))

        expected_records = [
            {
                "content": None,
                "type": "dependency",
                "operation": "dependency list",
                "dependency": name,
                "vcs": "git",
                "repository": "https://example.test/shared.git",
                "ref_kind": "branch",
                "ref": "main",
                "revision": revision * 40,
                "entries": entries,
                "modified": modified,
                "missing": missing,
                "unlocked": unlocked,
            }
            for name, revision, entries, modified, missing, unlocked in (
                ("ci", "1", 8, 3, 2, 1),
                ("z-unused", "2", 0, 0, 0, 0),
            )
        ]
        for _ in range(2):
            result = CliRunner().invoke(
                application.app,
                [
                    "-p",
                    "automation",
                    "--config",
                    str(config),
                    "--cache-dir",
                    str(config / "bad"),
                    "dependency",
                    "list",
                ],
            )

            assert result.exit_code == 0, result.output
            assert not result.stderr
            assert cell_payloads([json.loads(line) for line in result.stdout.splitlines()]) == expected_records
            assert not project_config.is_set()
            assert not active_workspace.is_set()
            assert not active_cache_root.is_set()
        assert {path: path.read_bytes() for path in config.parent.rglob("*") if path.is_file()} == before

    @pytest.mark.parametrize(
        "expected,files,directories,status",
        [
            (b"base", {"installed": b"base"}, (), "unmodified"),
            (b"base", {}, (), "missing"),
            (b"base", {}, ("installed",), "missing"),
            (b"base", {"installed/child": b"base"}, (), "modified"),
            ({"a": b"base", "nested/b": b"base"}, {}, (), "missing"),
            ({"a": b"base", "nested/b": b"base"}, {"installed/a": b"base"}, ("installed/nested/b",), "missing"),
            ({"a": b"base", "nested/b": b"base"}, {"installed/nested": b"base"}, (), "modified"),
            ({"a": b"base"}, {"installed": b""}, (), "modified"),
            ({}, {}, (), "unmodified"),
            ({}, {}, ("installed/empty/nested",), "unmodified"),
            ({}, {"installed": b""}, (), "modified"),
        ],
    )
    def test_file_and_directory_comparison(
        self,
        config: Path,
        expected: bytes | dict[str, bytes],
        files: dict[str, bytes],
        directories: tuple[str, ...],
        status: str,
    ) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=expected)
        for path in directories:
            (config.parent / path).mkdir(parents=True)
        for path, data in files.items():
            target = config.parent / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        code, records, output = invoke_dependency_list(config)

        assert code == 0, output
        assert records[0]["entries"] == 1
        assert {key: records[0][key] for key in ("modified", "missing", "unlocked")} == {
            key: int(key == status) for key in ("modified", "missing", "unlocked")
        }

    @pytest.mark.parametrize(
        "local,status", [("absent", "unmodified"), ("directory", "unmodified"), ("file", "modified")]
    )
    def test_expected_absence(self, config: Path, local: str, status: str) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=b"base")
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        recorded["entries"][0].update(kind="absent")
        recorded["entries"][0].pop("hash")
        lockfile.write_text(tomli_w.dumps(recorded))
        if local == "directory":
            (config.parent / "installed").mkdir()
        elif local == "file":
            (config.parent / "installed").write_bytes(b"")

        code, records, output = invoke_dependency_list(config)

        assert code == 0, output
        assert records[0]["modified"] == int(status == "modified")
        assert records[0]["missing"] == records[0]["unlocked"] == 0

    def test_regular_file_obstructing_an_entry_parent_is_modified(self, config: Path) -> None:
        declare_entry(config, source="@/source", destination="@/parent/installed", files=b"base")
        (config.parent / "parent").write_bytes(b"obstruction")

        code, records, output = invoke_dependency_list(config)

        assert code == 0, output
        assert records[0]["modified"] == 1
        assert records[0]["missing"] == records[0]["unlocked"] == 0

    @pytest.mark.parametrize("state", ["absent", "empty", "missing_dependency", "repository", "selector", "backend"])
    def test_missing_or_mismatching_dependency_lock_unlocks_all_its_entries(  # noqa: CCR001 — Stale-lock variants.
        self, config: Path, state: str
    ) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=b"base")
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        if state == "absent":
            lockfile.unlink()
        elif state in ("empty", "missing_dependency"):
            recorded["entries"] = []
            recorded["dependencies"].pop("ci")
            if state == "empty":
                recorded["dependencies"] = {}
            lockfile.write_text(tomli_w.dumps(recorded))
        else:
            locked = recorded["dependencies"]["ci"]
            if state == "repository":
                locked["repository"] = "https://example.test/other.git"
            elif state == "selector":
                locked["selector"] = {"kind": "tag", "value": "main"}
            else:
                locked.update(vcs="filesystem", repository="missing-source", revision=hashes.hash_file(b"base"))
                locked.pop("selector")
            lockfile.write_text(tomli_w.dumps(recorded))
        before = (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None)

        code, records, output = invoke_dependency_list(config)

        assert code == 0, output
        assert records[0]["revision"] is None
        assert records[0]["entries"] == records[0]["unlocked"] == 1
        assert records[0]["modified"] == records[0]["missing"] == 0
        assert (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None) == before

    @pytest.mark.parametrize(
        "field,value", [("source_path", "@/other"), ("destination", "@/other"), ("dependency", "z-unused")]
    )
    def test_mismatching_entry_baseline_is_unlocked(self, config: Path, field: str, value: str) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=b"base")
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        recorded["entries"][0][field] = value
        lockfile.write_text(tomli_w.dumps(recorded))

        code, records, output = invoke_dependency_list(config)

        assert code == 0, output
        assert records[0]["revision"] == "1" * 40
        assert records[0]["unlocked"] == 1
        assert records[0]["modified"] == records[0]["missing"] == 0

    @pytest.mark.parametrize("kind", ["branch", "tag", "revision"])
    def test_explicit_selectors(self, config: Path, kind: str) -> None:
        value = "1" * 40 if kind == "revision" else "release"
        for path in (config, config.with_name("vcsinstall.lock")):
            document = tomllib.loads(path.read_text())
            document["dependencies"]["ci"]["selector"] = {"kind": kind, "value": value}
            path.write_text(tomli_w.dumps(document))

        code, records, output = invoke_dependency_list(config)

        assert code == 0, output
        assert (records[0]["ref_kind"], records[0]["ref"], records[0]["revision"]) == (kind, value, "1" * 40)

    @pytest.mark.parametrize("lock_kind", ["branch", "tag", "revision", None])
    def test_omitted_git_selector_retains_only_a_matching_branch(self, config: Path, lock_kind: str | None) -> None:
        document = tomllib.loads(config.read_text())
        document["dependencies"]["ci"].pop("selector")
        config.write_text(tomli_w.dumps(document))
        lockfile = config.with_name("vcsinstall.lock")
        if lock_kind is None:
            lockfile.unlink()
        else:
            recorded = tomllib.loads(lockfile.read_text())
            recorded["dependencies"]["ci"]["selector"] = {
                "kind": lock_kind,
                "value": "1" * 40 if lock_kind == "revision" else "main",
            }
            lockfile.write_text(tomli_w.dumps(recorded))

        code, records, output = invoke_dependency_list(config)

        assert code == 0, output
        matching = lock_kind == "branch"
        assert records[0]["ref_kind"] == ("branch" if matching else "default")
        assert records[0]["ref"] == ("main" if matching else None)
        assert records[0]["revision"] == ("1" * 40 if matching else None)

    @pytest.mark.parametrize("locked", [False, True])
    @pytest.mark.parametrize("vcs", ["git", "filesystem"])
    def test_normalizes_repository_identity_against_the_selected_project(
        self, config: Path, monkeypatch: pytest.MonkeyPatch, locked: bool, vcs: str
    ) -> None:
        repository = config.parent.parent / "missing-source"
        definition: dict[str, object] = {"vcs": vcs, "repository": "../missing-source"}
        if vcs == "git":
            definition["selector"] = {"kind": "branch", "value": "main"}
        config.write_text(tomli_w.dumps({"version": 1, "dependencies": {"ci": definition}}))
        revision = "1" * 40 if vcs == "git" else hashes.hash_file(b"base")
        lockfile = config.with_name("vcsinstall.lock")
        if locked:
            recorded = {**definition, "repository": str(repository), "revision": revision}
            lockfile.write_text(tomli_w.dumps({"version": 1, "dependencies": {"ci": recorded}}))
        else:
            lockfile.unlink()
        nested = config.parent / "nested"
        nested.mkdir()
        monkeypatch.chdir(nested)

        code, records, output = invoke_dependency_list(config)

        assert code == 0, output
        assert records[0]["repository"] == str(repository)
        assert records[0]["revision"] == (revision if locked else None)
        assert not repository.exists()

    def test_discovery_uses_the_nearest_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "vcsinstall.toml").write_text("invalid parent configuration")
        project = tmp_path / "project"
        nested = project / "nested"
        nested.mkdir(parents=True)
        (project / "vcsinstall.toml").write_text("version = 1\n")
        monkeypatch.chdir(nested)

        result = CliRunner().invoke(application.app, ["-p", "automation", "dependency", "list"])

        assert result.exit_code == 0, result.output
        assert not result.stdout and not result.stderr
        assert not (project / "vcsinstall.lock").exists()

    @pytest.mark.parametrize("filename", ["custom.toml", "vcsinstall.lock"])
    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_invalid_project_files_fail_through_the_shared_protocol(
        self, config: Path, filename: str, protocol: str
    ) -> None:
        path = config.parent / filename
        path.write_text("version = [")

        result = CliRunner().invoke(application.app, ["-p", protocol, "--config", str(config), "dependency", "list"])

        assert result.exit_code == 2, result.output
        assert str(path) in result.stdout
        assert not result.stderr
        assert not project_config.is_set() and not active_workspace.is_set()
        if protocol == "automation":
            assert json.loads(result.stdout)["type"] == "error"
        elif protocol == "llm":
            assert "--VCSINSTALL-CELL " in result.stdout

    def test_missing_explicit_configuration_does_not_fall_back(self, config: Path) -> None:
        missing = config.parent / "missing.toml"

        code, records, output = invoke_dependency_list(missing)

        assert code == 2, output
        assert records[0]["type"] == "error"
        assert records[0]["path"] == str(missing)
        assert not missing.exists()

    @pytest.mark.parametrize("locked", [False, True])
    def test_unreadable_local_content_fails_instead_of_becoming_missing_or_unlocked(
        self, config: Path, mocker: MockerFixture, locked: bool
    ) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=b"base")
        target = config.parent / "installed"
        target.write_bytes(b"base")
        if not locked:
            config.with_name("vcsinstall.lock").unlink()
        before = config.read_bytes()
        mocker.patch(
            "vcsinstall.filesystem.client.hashlib.file_digest", side_effect=PermissionError("Unreadable file")
        )

        code, records, output = invoke_dependency_list(config)

        assert code == 3, output
        assert [record["type"] for record in records] == ["error"]
        assert records[0]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert records[0]["path"] == str(target)
        assert config.read_bytes() == before
        assert target.read_bytes() == b"base"

    @pytest.mark.parametrize("child", [False, True])
    def test_symlinks_in_selected_content_fail(self, config: Path, child: bool) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files={"a": b"base"})
        target = config.parent / "unmanaged"
        target.write_bytes(b"base")
        link = config.parent / "installed"
        if child:
            link.mkdir()
            link = link / "a"
        link.symlink_to(target)

        code, records, output = invoke_dependency_list(config)

        assert code == 3, output
        assert [record["type"] for record in records] == ["error"]
        assert link.is_symlink()
        assert target.read_bytes() == b"base"

    @pytest.mark.parametrize("arguments", [["ci"], ["--force"], ["--outdated"], ["--protocol", "automation"]])
    def test_rejects_arguments_and_options_before_loading(self, arguments: list[str], mocker: MockerFixture) -> None:
        load = mocker.patch.object(dependency, "load_workspace", side_effect=AssertionError("Unexpected loading"))

        result = CliRunner().invoke(application.app, ["dependency", "list", *arguments])

        assert result.exit_code == 2
        load.assert_not_called()

    @pytest.mark.parametrize("help_option", ["--help", "-h"])
    def test_help_requires_no_project(self, help_option: str, mocker: MockerFixture) -> None:
        load = mocker.patch.object(dependency, "load_workspace", side_effect=AssertionError("Unexpected loading"))

        result = CliRunner().invoke(
            application.app, ["--config", "/missing/config", "dependency", "list", help_option]
        )

        assert result.exit_code == 0, result.output
        assert "locked revisions" in result.stdout
        load.assert_not_called()

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_success_uses_shared_protocols(self, config: Path, protocol: str) -> None:
        result = CliRunner().invoke(application.app, ["-p", protocol, "--config", str(config), "dependency", "list"])

        assert result.exit_code == 0, result.output
        assert not result.stderr
        assert "ci" in result.stdout and "z-unused" in result.stdout
        if protocol == "automation":
            assert all(json.loads(line)["content"] is None for line in result.stdout.splitlines())
        elif protocol == "llm":
            assert "--VCSINSTALL-CELL " in result.stdout


@pytest.mark.usefixtures("isolated_settings")
class TestOutdatedDependencies:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "custom.toml"
        config.write_text("# Preserve this configuration.\nversion = 1\n")
        return config

    @pytest.fixture
    def client(self, tmp_path: Path) -> GitClient:
        return create_repository(tmp_path / "source")

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("selector", ["branch", "tag", "annotated"])
    def test_reports_moved_selectors_even_when_content_is_identical(  # noqa: CCR001 — Selector variants.
        self, config: Path, tmp_path: Path, object_format: str, selector: str
    ) -> None:
        client = create_repository(tmp_path / "source", object_format=object_format)
        if selector != "branch":
            client.create_tag("release", message="Release" if selector == "annotated" else None).unwrap()
        reference = "#branch=trunk" if selector == "branch" else "#tag=release"
        cache = tmp_path / "cache"
        code, added, output = invoke_add(config, cache, "ci", "git+" + client.path.as_uri() + reference)
        assert code == 0, output
        locked_revision = added[0]["revision"]
        client.run(["commit", "--allow-empty", "-m", "New revision, same tree"]).unwrap()
        current_revision = client.resolve_commit("HEAD").unwrap()
        if selector != "branch":
            client.run(["tag", "--force", "release"]).unwrap()
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

        for _ in range(2):
            code, records, output = invoke_dependency_outdated(config, cache)

            assert code == 0, output
            assert cell_payloads(records) == [
                {
                    "type": "dependency",
                    "content": None,
                    "operation": "dependency outdated",
                    "dependency": "ci",
                    "vcs": "git",
                    "repository": str(client.path),
                    "ref_kind": "branch" if selector == "branch" else "tag",
                    "ref": "trunk" if selector == "branch" else "release",
                    "revision": locked_revision,
                    "current_revision": current_revision,
                }
            ]
            assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
            assert not project_config.is_set()
            assert not active_workspace.is_set()
            assert not active_cache_root.is_set()

    @pytest.mark.parametrize("names", [[], ["z-last", "a-first", "z-last"]])
    def test_selects_all_or_unique_named_dependencies_in_name_order(
        self, config: Path, tmp_path: Path, client: GitClient, names: list[str]
    ) -> None:
        cache = tmp_path / "cache"
        revision = client.resolve_commit("HEAD").unwrap()
        for name in ("z-last", "a-first", "unselected"):
            assert invoke_add(config, cache, name, str(client.path))[0] == 0
        assert invoke_add(config, cache, "--rev", revision, "pinned", str(client.path))[0] == 0
        (client.path / "guide.txt").write_bytes(b"changed")
        client.commit("Advance branch", all_tracked=True).unwrap()

        code, records, output = invoke_dependency_outdated(config, cache, *names)

        assert code == 0, output
        assert [record["dependency"] for record in records] == (
            ["a-first", "z-last"] if names else ["a-first", "unselected", "z-last"]
        )

    def test_unchanged_selector_and_empty_project_emit_no_records(
        self, config: Path, tmp_path: Path, client: GitClient
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_dependency_outdated(config, cache)[:2] == (0, [])
        assert not config.with_name("vcsinstall.lock").exists()
        assert not cache.exists()
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0

        assert invoke_dependency_outdated(config, cache)[:2] == (0, [])

    def test_revision_pin_uses_available_cache_without_refresh(
        self, config: Path, tmp_path: Path, client: GitClient, mocker: MockerFixture
    ) -> None:
        cache = tmp_path / "cache"
        revision = client.resolve_commit("HEAD").unwrap()
        assert invoke_add(config, cache, "--rev", revision, "ci", str(client.path))[0] == 0
        (client.path / "guide.txt").write_bytes(b"newer")
        client.commit("Newer source revision", all_tracked=True).unwrap()
        client.path.rename(tmp_path / "unavailable-source")
        fetch = mocker.spy(GitClient, "fetch_references")
        refresh_head = mocker.spy(GitClient, "refresh_remote_head")

        code, records, output = invoke_dependency_outdated(config, cache)

        assert code == 0, output
        assert not records
        fetch.assert_not_called()
        refresh_head.assert_not_called()

    @pytest.mark.parametrize("fresh_cache", [False, True])
    def test_omitted_git_selector_keeps_recorded_branch_after_default_changes(
        self, config: Path, tmp_path: Path, client: GitClient, fresh_cache: bool
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
        declarations = tomllib.loads(config.read_text())
        del declarations["dependencies"]["ci"]["selector"]
        declarations["dependencies"]["ci"]["repository"] = "../source"
        config.write_text(tomli_w.dumps(declarations))
        client.create_branch("new-default").unwrap()
        client.set_symbolic_reference("HEAD", "refs/heads/new-default").unwrap()
        client.run(["commit", "--allow-empty", "-m", "New default branch revision"]).unwrap()
        original = client.resolve_commit("trunk").unwrap()
        client.set_symbolic_reference("HEAD", "refs/heads/trunk").unwrap()
        client.run(["commit", "--allow-empty", "-m", "Tracked branch revision"]).unwrap()
        current_revision = client.resolve_commit("trunk").unwrap()
        client.set_symbolic_reference("HEAD", "refs/heads/new-default").unwrap()
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

        code, records, output = invoke_dependency_outdated(config, tmp_path / "fresh-cache" if fresh_cache else cache)

        assert code == 0, output
        assert len(records) == 1
        assert records[0]["ref_kind"] == "branch"
        assert records[0]["ref"] == "trunk"
        assert records[0]["revision"] == original
        assert records[0]["current_revision"] == current_revision
        assert records[0]["repository"] == str(client.path)
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_branch_rewind_is_outdated(self, config: Path, tmp_path: Path, client: GitClient) -> None:
        original = client.resolve_commit("HEAD").unwrap()
        client.run(["commit", "--allow-empty", "-m", "Advance source"]).unwrap()
        cache = tmp_path / "cache"
        code, added, output = invoke_add(config, cache, "ci", str(client.path))
        assert code == 0, output
        client.run(["update-ref", "refs/heads/trunk", original]).unwrap()

        code, records, output = invoke_dependency_outdated(config, cache)

        assert code == 0, output
        assert records[0]["revision"] == added[0]["revision"]
        assert records[0]["current_revision"] == original

    def test_filesystem_checks_complete_live_fingerprint_without_local_entry_inspection(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide.txt").write_bytes(b"original")
        cache = tmp_path / "cache"
        code, added, output = invoke_add(config, cache, "ci", source.as_uri())
        assert code == 0, output
        declare_entry(config, source="@/guide.txt", destination="@/installed", files=b"original")
        declarations = tomllib.loads(config.read_text())
        declarations["entries"].append({"dependency": "ci", "source_path": "@/missing", "destination": "@/unlocked"})
        config.write_text(tomli_w.dumps(declarations))
        installed = config.parent / "installed"
        installed.write_bytes(b"local modifications")
        (source / "uninstalled.txt").write_bytes(b"change outside installed entry")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes(), installed.read_bytes())
        inspect = mocker.spy(FilesystemSourceAdapter, "inspect")
        mocker.patch("vcsinstall.runtime.commands.FSClient", side_effect=AssertionError("Unexpected local inspection"))
        mocker.patch("vcsinstall.vcs_clients.GitClient.run", side_effect=AssertionError("Unexpected Git access"))

        code, records, output = invoke_dependency_outdated(config, cache)

        assert code == 0, output
        assert len(records) == 1
        assert records[0]["revision"] == added[0]["revision"]
        assert records[0]["current_revision"] != records[0]["revision"]
        current_revision = records[0]["current_revision"]
        assert isinstance(current_revision, str)
        assert current_revision.startswith("sha256:")
        assert records[0]["ref_kind"] == "default"
        assert records[0]["ref"] is None
        inspect.assert_not_called()
        assert not cache.exists()
        assert (
            config.read_bytes(),
            config.with_name("vcsinstall.lock").read_bytes(),
            installed.read_bytes(),
        ) == before

    @pytest.mark.parametrize("state", ["missing-file", "missing-lock", "repository", "selector", "backend"])
    def test_all_selected_locks_are_required_before_source_acquisition(  # noqa: CCR001 — Lock variants.
        self, config: Path, tmp_path: Path, client: GitClient, mocker: MockerFixture, state: str
    ) -> None:
        cache = tmp_path / "cache"
        for name in ("a-valid", "z-invalid"):
            assert invoke_add(config, cache, name, str(client.path))[0] == 0
        lockfile = config.with_name("vcsinstall.lock")
        locks = tomllib.loads(lockfile.read_text())
        if state == "missing-file":
            lockfile.unlink()
        elif state == "missing-lock":
            del locks["dependencies"]["z-invalid"]
            lockfile.write_text(tomli_w.dumps(locks))
        else:
            declarations = tomllib.loads(config.read_text())
            definition = declarations["dependencies"]["z-invalid"]
            if state == "repository":
                definition["repository"] = str(tmp_path / "other-source")
            elif state == "selector":
                definition["selector"] = {"kind": "branch", "value": "other"}
            else:
                definition["vcs"] = "filesystem"
                del definition["selector"]
            config.write_text(tomli_w.dumps(declarations))
        before = (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None)
        acquire = mocker.patch(
            "vcsinstall.hosting.GitHostingAdapter.acquire", side_effect=AssertionError("Unexpected fetch")
        )

        code, records, output = invoke_dependency_outdated(config, cache)

        assert code == ExitCode.environment_error, output
        record = next(
            record
            for record in records
            if record["code"] == workspace_errors.DependencyLockRequired.model_fields["code"].default
        )
        assert record["dependency"] == ("a-valid" if state == "missing-file" else "z-invalid")
        assert record["path"] == str(lockfile)
        diagnostic = record["content"]
        assert isinstance(diagnostic, str)
        assert "install" in diagnostic
        assert "dependency update" in diagnostic
        acquire.assert_not_called()
        assert (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None) == before

    def test_unknown_name_fails_before_any_source_access(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        acquire = mocker.patch(
            "vcsinstall.runtime.commands.get_hosting", side_effect=AssertionError("Unexpected fetch")
        )

        code, records, output = invoke_dependency_outdated(config, tmp_path / "cache", "unknown", "unknown")

        assert code == ExitCode.invalid_arguments, output
        assert len(records) == 1
        assert records[0]["code"] == InvalidArguments.model_fields["code"].default
        diagnostic = records[0]["content"]
        assert isinstance(diagnostic, str)
        assert "unknown" in diagnostic
        acquire.assert_not_called()
        assert not config.with_name("vcsinstall.lock").exists()

    @pytest.mark.parametrize("failure", ["refresh", "branch", "tag"])
    def test_source_failures_do_not_use_stale_references_or_emit_partial_success(
        self, config: Path, tmp_path: Path, client: GitClient, failure: str
    ) -> None:
        cache = tmp_path / "cache"
        client.create_tag("release").unwrap()
        for name, reference in (("a-outdated", "#tag=release"), ("z-failure", "#branch=trunk")):
            assert invoke_add(config, cache, name, "git+" + client.path.as_uri() + reference)[0] == 0
        client.run(["commit", "--allow-empty", "-m", "Advance source"]).unwrap()
        client.run(["tag", "--force", "release"]).unwrap()
        if failure == "refresh":
            client.path.rename(tmp_path / "unavailable-source")
        elif failure == "branch":
            client.create_branch("replacement").unwrap()
            client.set_symbolic_reference("HEAD", "refs/heads/replacement").unwrap()
            client.run(["update-ref", "-d", "refs/heads/trunk"]).unwrap()
        else:
            client.run(["tag", "--delete", "release"]).unwrap()
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

        code, records, output = invoke_dependency_outdated(config, cache)

        assert code == ExitCode.environment_error, output
        assert all(record["type"] == "error" for record in records)
        assert records[0]["dependency"] == ("z-failure" if failure == "branch" else "a-outdated")
        assert any(record["code"] == vcs_errors.CommandFailed.model_fields["code"].default for record in records)
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert not active_cache_root.is_set()
        assert not active_workspace.is_set()
        assert not project_config.is_set()

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_protocols_preserve_both_revisions_on_stdout(
        self, config: Path, tmp_path: Path, client: GitClient, protocol: str
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
        locked_revision = client.resolve_commit("HEAD").unwrap()
        client.run(["commit", "--allow-empty", "-m", "Advance source"]).unwrap()
        current_revision = client.resolve_commit("HEAD").unwrap()

        result = CliRunner().invoke(
            application.app,
            ["-p", protocol, "--config", str(config), "--cache-dir", str(cache), "dependency", "outdated"],
        )

        assert result.exit_code == 0, result.output
        assert not result.stderr
        assert locked_revision in result.stdout
        assert current_revision in result.stdout
        if protocol == "llm":
            assert "--VCSINSTALL-CELL " in result.stdout

    @pytest.mark.parametrize("arguments", [["--force"], ["--rev", "abcd"], ["--protocol", "automation"], ["BadName"]])
    def test_rejects_invalid_command_shape_without_project_loading(self, arguments: list[str]) -> None:
        result = CliRunner().invoke(
            application.app, ["--config", "/missing/config", "dependency", "outdated", *arguments]
        )

        assert result.exit_code != 0
        assert "No such option" in result.output or "Invalid value" in result.output

    def test_missing_configuration_retains_shared_diagnostic(self, tmp_path: Path) -> None:
        code, records, output = invoke_dependency_outdated(tmp_path / "missing.toml", tmp_path / "cache")

        assert code == ExitCode.configuration_error, output
        assert records[0]["code"] == config_errors.Unreadable.model_fields["code"].default
        assert not (tmp_path / "cache").exists()


@pytest.mark.usefixtures("isolated_settings")
class TestUpdateDependencies:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "custom.toml"
        config.write_text("# Preserve configuration bytes.\nversion = 1\n")
        return config

    @pytest.fixture
    def client(self, tmp_path: Path) -> GitClient:
        return create_repository(tmp_path / "source")

    @pytest.mark.parametrize("names", [[], ["z-last", "a-first", "z-last"]])
    def test_updates_all_or_unique_names_and_orders_both_record_types(  # noqa: CCR001 — Selection variants.
        self, config: Path, tmp_path: Path, client: GitClient, names: list[str], mocker: MockerFixture
    ) -> None:
        cache = tmp_path / "cache"
        original = client.resolve_commit("HEAD").unwrap()
        for name, destination in (("z-last", "@/a"), ("a-first", "@/z"), ("unselected", "@/middle")):
            assert invoke_add(config, cache, name, str(client.path))[0] == 0
            assert invoke_entry_add(config, cache, name + "@/guide.txt", destination)[0] == 0
        (client.path / "guide.txt").write_bytes(b"upstream\n")
        client.commit("Update guides", all_tracked=True).unwrap()
        revision = client.resolve_commit("HEAD").unwrap()
        before = config.read_bytes()
        resolve = mocker.spy(dependency.get_adapter(VcsId.git), "resolve")

        code, records, output = invoke_dependency_update(config, cache, *names)

        assert code == 0, output
        selected = ["a-first", "z-last"] if names else ["a-first", "unselected", "z-last"]
        assert [row["dependency"] for row in records if row["type"] == "dependency"] == selected
        assert [row["destination"] for row in records if row["type"] == "installation"] == (
            ["@/a", "@/z"] if names else ["@/a", "@/middle", "@/z"]
        )
        assert [row["type"] for row in records] == ["dependency"] * len(selected) + ["installation"] * len(selected)
        assert all(row["operation"] == "dependency update" and row["outcome"] == "updated" for row in records)
        assert all(row["revision"] == revision and row["previous_revision"] == original for row in records)
        assert all(row["content"] is None for row in records)
        assert resolve.call_count == len(selected)
        locked = tomllib.loads(config.with_name("vcsinstall.lock").read_text())
        for name, destination in (("z-last", "a"), ("a-first", "z"), ("unselected", "middle")):
            assert locked["dependencies"][name]["revision"] == (revision if name in selected else original)
            assert (config.parent / destination).read_bytes() == (b"upstream\n" if name in selected else b"original\n")
        assert config.read_bytes() == before
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()

    @pytest.mark.parametrize("object_format", ["sha1", "sha256"])
    @pytest.mark.parametrize("selector", ["branch", "tag", "annotated", "revision"])
    def test_tracks_literal_selectors_and_keeps_immutable_pins(  # noqa: CCR001 — Selector variants.
        self, config: Path, tmp_path: Path, object_format: str, selector: str
    ) -> None:
        client = create_repository(tmp_path / "source", object_format=object_format)
        original = client.resolve_commit("HEAD").unwrap()
        if selector in ("tag", "annotated"):
            client.create_tag("release", message="Release" if selector == "annotated" else None).unwrap()
        reference = (
            "#rev=" + original
            if selector == "revision"
            else "#branch=trunk" if selector == "branch" else "#tag=release"
        )
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", "git+" + client.path.as_uri() + reference)[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        (client.path / "guide.txt").write_bytes(b"upstream\n")
        client.commit("Advance source", all_tracked=True).unwrap()
        current_revision = client.resolve_commit("HEAD").unwrap()
        if selector in ("tag", "annotated"):
            client.run(["tag", "--force", "release"]).unwrap()

        code, records, output = invoke_dependency_update(config, cache)

        assert code == 0, output
        assert len(records) == 2
        assert all(row["revision"] == (original if selector == "revision" else current_revision) for row in records)
        assert all(row["outcome"] == ("unchanged" if selector == "revision" else "updated") for row in records)
        assert (config.parent / "installed").read_bytes() == (
            b"original\n" if selector == "revision" else b"upstream\n"
        )
        assert config.read_bytes() == before[0]
        if selector == "revision":
            assert config.with_name("vcsinstall.lock").read_bytes() == before[1]

    def test_empty_project_has_no_records_or_effects(self, config: Path, tmp_path: Path) -> None:
        before = config.read_bytes()
        cache = tmp_path / "cache"

        assert invoke_dependency_update(config, cache)[:2] == (0, [])

        assert config.read_bytes() == before
        assert not config.with_name("vcsinstall.lock").exists()
        assert not cache.exists()

    @pytest.mark.parametrize("prior_lock", [False, True])
    def test_locks_dependencies_without_entries_without_changing_configuration(
        self, config: Path, tmp_path: Path, client: GitClient, prior_lock: bool
    ) -> None:
        cache = tmp_path / "cache"
        if prior_lock:
            assert invoke_add(config, cache, "--branch", "trunk", "ci", str(client.path))[0] == 0
        config.write_text('version = 1\n[dependencies.ci]\nvcs = "git"\nrepository = "../source"\n')
        before = config.read_bytes()

        code, records, output = invoke_dependency_update(config, cache)

        assert code == 0, output
        assert len(records) == 1
        assert records[0]["type"] == "dependency"
        assert records[0]["outcome"] == ("unchanged" if prior_lock else "updated")
        assert records[0]["ref_kind"] == "branch"
        assert records[0]["ref"] == "trunk"
        assert records[0]["previous_revision"] == (records[0]["revision"] if prior_lock else None)
        assert config.read_bytes() == before

    @pytest.mark.parametrize("fresh_cache", [False, True])
    def test_omitted_git_selector_retains_its_recorded_branch(
        self, config: Path, tmp_path: Path, client: GitClient, fresh_cache: bool
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
        declarations = tomllib.loads(config.read_text())
        del declarations["dependencies"]["ci"]["selector"]
        config.write_text(tomli_w.dumps(declarations))
        client.create_branch("new-default", checkout=True).unwrap()
        client.run(["commit", "--allow-empty", "-m", "New default"]).unwrap()
        client.run(["checkout", "trunk"]).unwrap()
        client.run(["commit", "--allow-empty", "-m", "Tracked revision"]).unwrap()
        revision = client.resolve_commit("HEAD").unwrap()
        client.run(["checkout", "new-default"]).unwrap()
        before = config.read_bytes()

        code, records, output = invoke_dependency_update(config, tmp_path / "fresh-cache" if fresh_cache else cache)

        assert code == 0, output
        assert records[0]["ref"] == "trunk"
        assert records[0]["revision"] == revision
        assert config.read_bytes() == before

    @pytest.mark.parametrize("local", ["baseline", "missing", "selected", "conflict"])
    def test_filesystem_updates_protect_local_content_and_restore_missing_files(  # noqa: CCR001 — Content variants.
        self, config: Path, tmp_path: Path, local: str, mocker: MockerFixture
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide.txt").write_bytes(b"original")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        installed = config.parent / "installed"
        if local == "missing":
            installed.unlink()
        elif local in ("selected", "conflict"):
            installed.write_bytes(b"upstream" if local == "selected" else b"local edit")
        (source / "guide.txt").write_bytes(b"upstream")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        remove = mocker.spy(FSClient, "remove")
        write = mocker.spy(FSClient, "write_stream")

        code, records, output = invoke_dependency_update(config, cache)

        assert code == (3 if local == "conflict" else 0), output
        assert config.read_bytes() == before[0]
        assert not cache.exists()
        if local == "conflict":
            assert any(
                row.get("code") == runtime_errors.LocalConflict(destination="@/installed").code for row in records
            )
            assert installed.read_bytes() == b"local edit"
            assert config.with_name("vcsinstall.lock").read_bytes() == before[1]
        else:
            assert installed.read_bytes() == b"upstream"
            assert all(row["outcome"] == "updated" for row in records)
        if local in ("selected", "conflict"):
            remove.assert_not_called()
            write.assert_not_called()

    def test_directory_update_replaces_whole_changed_entry_and_records_complete_manifest(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "a").write_bytes(b"old")
        (source / "b").write_bytes(b"unchanged")
        (source / "deleted").write_bytes(b"remove")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "ci@/", "@/installed")[0] == 0
        (config.parent / "outside").write_bytes(b"preserve")
        (config.parent / "installed" / "b").unlink()
        (source / "a").write_bytes(b"new")
        (source / "deleted").unlink()
        (source / ".new").write_bytes(b"addition")
        write = mocker.spy(FSClient, "write_stream")

        code, records, output = invoke_dependency_update(config, cache)

        assert code == 0, output
        assert (config.parent / "installed" / "a").read_bytes() == b"new"
        assert (config.parent / "installed" / "b").read_bytes() == b"unchanged"
        assert not (config.parent / "installed" / "deleted").exists()
        assert (config.parent / "installed" / ".new").read_bytes() == b"addition"
        assert (config.parent / "outside").read_bytes() == b"preserve"
        assert sorted(call.args[1] for call in write.call_args_list) == [
            "@/installed/.new",
            "@/installed/a",
            "@/installed/b",
        ]
        locked = tomllib.loads(config.with_name("vcsinstall.lock").read_text())
        assert [file["path"] for file in locked["entries"][0]["files"]] == ["@/.new", "@/a", "@/b"]
        assert all(row["revision"] == locked["dependencies"]["ci"]["revision"] for row in records)

    @pytest.mark.parametrize("local", ["mixed", "addition", "modified_and_missing"])
    def test_directory_conflicts_are_not_merged_file_by_file(self, config: Path, tmp_path: Path, local: str) -> None:
        source = tmp_path / "source"
        source.mkdir()
        for name in ("a", "b"):
            (source / name).write_bytes(b"old")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "ci@/", "@/installed")[0] == 0
        installed = config.parent / "installed"
        if local == "mixed":
            (installed / "a").write_bytes(b"new")
        elif local == "addition":
            (installed / ".local").write_bytes(b"preserve")
        else:
            (installed / "a").write_bytes(b"local edit")
            (installed / "b").unlink()
        for name in ("a", "b"):
            (source / name).write_bytes(b"new")
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        files_before = {path.name: path.read_bytes() for path in installed.iterdir()}

        code, records, output = invoke_dependency_update(config, cache)

        assert code == 3, output
        assert any(row.get("code") == runtime_errors.LocalConflict(destination="@/installed").code for row in records)
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert {path.name: path.read_bytes() for path in installed.iterdir()} == files_before

    @pytest.mark.parametrize("initial", ["file", "directory"])
    def test_type_changes_source_deletion_and_recreation_keep_the_mapping(
        self, config: Path, tmp_path: Path, initial: str
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        selected = source / "selected"
        if initial == "file":
            selected.write_bytes(b"original")
        else:
            selected.mkdir()
            (selected / "child").write_bytes(b"original")
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "ci@/selected", "@/installed")[0] == 0
        before = config.read_bytes()
        installed = config.parent / "installed"
        if initial == "file":
            selected.unlink()
            selected.mkdir()
            (selected / "child").write_bytes(b"replacement")
        else:
            (selected / "child").unlink()
            selected.rmdir()
            selected.write_bytes(b"replacement")
        code, _, output = invoke_dependency_update(config, cache)
        assert code == 0, output
        assert (installed / "child" if initial == "file" else installed).read_bytes() == b"replacement"

        if selected.is_dir():
            (selected / "child").unlink()
            selected.rmdir()
        else:
            selected.unlink()
        code, _, output = invoke_dependency_update(config, cache)
        assert code == 0, output
        assert not installed.exists()
        locked = tomllib.loads(config.with_name("vcsinstall.lock").read_text())
        assert locked["entries"][0]["kind"] == "absent"
        unchanged_lock = config.with_name("vcsinstall.lock").read_bytes()
        assert all(row["outcome"] == "unchanged" for row in invoke_dependency_update(config, cache)[1])
        assert config.with_name("vcsinstall.lock").read_bytes() == unchanged_lock

        selected.write_bytes(b"restored")
        code, _, output = invoke_dependency_update(config, cache)
        assert code == 0, output
        assert installed.read_bytes() == b"restored"
        assert config.read_bytes() == before

    @pytest.mark.parametrize("failure", ["conflict", "missing_baseline", "unavailable", "unsupported"])
    def test_late_preparation_failure_preserves_every_selected_dependency(
        self, config: Path, tmp_path: Path, failure: str
    ) -> None:
        cache = tmp_path / "cache"
        clients = {name: create_repository(tmp_path / name) for name in ("a", "z")}
        for name, client in clients.items():
            assert invoke_add(config, cache, name, str(client.path))[0] == 0
            assert invoke_entry_add(config, cache, name + "@/guide.txt", "@/" + name)[0] == 0
            (client.path / "guide.txt").write_bytes(b"updated")
            client.commit("Advance", all_tracked=True).unwrap()
        if failure == "conflict":
            (config.parent / "z").write_bytes(b"local edit")
        elif failure == "missing_baseline":
            locked = tomllib.loads(config.with_name("vcsinstall.lock").read_text())
            locked["entries"] = locked["entries"][:1]
            config.with_name("vcsinstall.lock").write_text(tomli_w.dumps(locked))
        elif failure == "unavailable":
            clients["z"].path.rename(tmp_path / "unavailable")
        else:
            guide = clients["z"].path / "guide.txt"
            guide.unlink()
            guide.symlink_to("missing")
            clients["z"].commit("Unsupported symlink", all_tracked=True).unwrap()
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        files = [(config.parent / name).read_bytes() for name in clients]

        code, records, output = invoke_dependency_update(config, cache)

        assert code == 3, output
        assert all(row["type"] == "error" for row in records)
        assert records[0]["dependency"] == "z"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert [(config.parent / name).read_bytes() for name in clients] == files
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()

    @pytest.mark.parametrize("operation", ["update", "redefine"])
    def test_unchanged_revision_rejects_inconsistent_baseline_content(
        self, config: Path, tmp_path: Path, client: GitClient, operation: str
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        lock = config.with_name("vcsinstall.lock")
        locked = tomllib.loads(lock.read_text())
        locked["entries"][0]["hash"] = hashes.hash_file(b"corrupt baseline")
        lock.write_text(tomli_w.dumps(locked))
        before = lock.read_bytes()

        code, records, output = (
            invoke_dependency_update(config, cache)
            if operation == "update"
            else invoke_add(config, cache, "--redefine", "ci", str(client.path))
        )

        assert code == 3, output
        assert any(
            row.get("code") == runtime_errors.SourceIntegrityMismatch(destination=ProjectPathId("@/installed")).code
            for row in records
        )
        assert (config.parent / "installed").read_bytes() == b"original\n"
        assert lock.read_bytes() == before

    @pytest.mark.parametrize("operation", ["update", "redefine"])
    def test_empty_directory_existence_does_not_change_baselines_or_write_files(
        self, config: Path, tmp_path: Path, mocker: MockerFixture, operation: str
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "empty").mkdir()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "ci@/empty", "@/installed")[0] == 0
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())
        (source / "empty").rmdir()
        (config.parent / "installed").mkdir()
        write = mocker.spy(FSClient, "write_stream")
        remove = mocker.spy(FSClient, "remove")

        code, records, output = (
            invoke_dependency_update(config, cache)
            if operation == "update"
            else invoke_add(config, cache, "--redefine", "ci", source.as_uri())
        )

        assert code == 0, output
        assert all(row["outcome"] == "unchanged" for row in records)
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        write.assert_not_called()
        remove.assert_not_called()

    def test_revision_only_updates_leave_matching_entries_untouched_and_preserve_unchanged_bytes(
        self, config: Path, tmp_path: Path, client: GitClient, mocker: MockerFixture
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        client.run(["commit", "--allow-empty", "-m", "Same content"]).unwrap()
        installed = config.parent / "installed"
        before = installed.stat().st_mtime_ns
        remove = mocker.spy(FSClient, "remove")
        write = mocker.spy(FSClient, "write_stream")

        code, records, output = invoke_dependency_update(config, cache)

        assert code == 0, output
        assert all(row["outcome"] == "updated" for row in records)
        assert installed.stat().st_mtime_ns == before
        remove.assert_not_called()
        write.assert_not_called()
        lock = config.with_name("vcsinstall.lock")
        lock.write_bytes(b"# Keep this comment.\n" + lock.read_bytes())
        before_lock = lock.read_bytes()
        code, records, output = invoke_dependency_update(config, cache)
        assert code == 0, output
        assert all(row["outcome"] == "unchanged" for row in records)
        assert lock.read_bytes() == before_lock

    def test_cached_immutable_pin_needs_no_refresh_or_accessible_original_repository(
        self, config: Path, tmp_path: Path, client: GitClient, mocker: MockerFixture
    ) -> None:
        cache = tmp_path / "cache"
        revision = client.resolve_commit("HEAD").unwrap()
        assert invoke_add(config, cache, "--rev", revision, "ci", str(client.path))[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        (config.parent / "installed").unlink()
        client.path.rename(tmp_path / "unavailable-source")
        fetch = mocker.spy(GitClient, "fetch_references")
        refresh_head = mocker.spy(GitClient, "refresh_remote_head")

        code, records, output = invoke_dependency_update(config, cache)

        assert code == 0, output
        assert all(row["revision"] == revision for row in records)
        assert (config.parent / "installed").read_bytes() == b"original\n"
        fetch.assert_not_called()
        refresh_head.assert_not_called()

    @pytest.mark.parametrize("change", ["repository", "source_path", "selector", "backend"])
    def test_explicit_declaration_changes_use_old_baselines_to_protect_content(
        self, config: Path, tmp_path: Path, client: GitClient, change: str
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        declarations = tomllib.loads(config.read_text())
        if change == "repository":
            replacement = tmp_path / "replacement"
            create_repository(replacement)
            (replacement / "guide.txt").write_bytes(b"new source")
            replacement_client = GitClient(path=replacement, repository=RepositoryLocator(str(replacement)))
            replacement_client.commit("Replacement content", all_tracked=True).unwrap()
            declarations["dependencies"]["ci"]["repository"] = str(replacement)
            client.path.rename(tmp_path / "unavailable-source")
        elif change == "source_path":
            (client.path / "new.txt").write_bytes(b"new source")
            client.add("new.txt").unwrap()
            client.commit("New mapping").unwrap()
            declarations["entries"][0]["source_path"] = "@/new.txt"
        elif change == "selector":
            client.create_branch("stable", checkout=True).unwrap()
            (client.path / "guide.txt").write_bytes(b"new source")
            client.commit("Stable content", all_tracked=True).unwrap()
            declarations["dependencies"]["ci"]["selector"]["value"] = "stable"
        else:
            replacement = tmp_path / "replacement"
            replacement.mkdir()
            (replacement / "guide.txt").write_bytes(b"new source")
            declarations["dependencies"]["ci"] = {"vcs": "filesystem", "repository": str(replacement)}
        config.write_text(tomli_w.dumps(declarations))
        before = config.read_bytes()

        code, records, output = invoke_dependency_update(config, cache)

        assert code == 0, output
        assert (config.parent / "installed").read_bytes() == b"new source"
        assert all(row["outcome"] == "updated" for row in records)
        assert config.read_bytes() == before

    @pytest.mark.parametrize("argument,expected_code", [("unknown", 1), ("@/installed", 2)])
    def test_unknown_names_fail_before_source_access(
        self, config: Path, tmp_path: Path, client: GitClient, argument: str, expected_code: int
    ) -> None:
        assert invoke_add(config, tmp_path / "original-cache", "ci", str(client.path))[0] == 0
        cache = tmp_path / "unused-cache"
        before = (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes())

        code, records, output = invoke_dependency_update(config, cache, "ci", argument, argument)

        assert code == expected_code, output
        if expected_code == 1:
            assert len(records) == 1
            assert records[0]["code"] == InvalidArguments(reason="").code
        assert not cache.exists()
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    @pytest.mark.parametrize("arguments", [["--force"], ["--rev", "abc"], ["--allow-outdated"]])
    def test_rejects_command_specific_options(self, tmp_path: Path, arguments: list[str]) -> None:
        code, _, _ = invoke_dependency_update(tmp_path / "missing.toml", tmp_path / "cache", *arguments)
        assert code == 2
        assert not tuple(tmp_path.iterdir())

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_protocols_preserve_update_fields(
        self, config: Path, tmp_path: Path, client: GitClient, protocol: str
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "ci", str(client.path))[0] == 0

        result = CliRunner().invoke(
            application.app,
            ["-p", protocol, "--config", str(config), "--cache-dir", str(cache), "dependency", "update"],
        )

        assert result.exit_code == 0, result.output
        assert "dependency update" in result.stdout
        assert "previous_revision" in result.stdout
        assert "unchanged" in result.stdout


class TestRemoveDependency:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "custom.toml"
        definition = {
            "vcs": "git",
            "repository": "https://example.test/shared.git",
            "selector": {"kind": "branch", "value": "main"},
        }
        config.write_text(
            tomli_w.dumps({"version": 1, "dependencies": dict.fromkeys(("ci", "formatting"), definition)})
        )
        config.with_name("vcsinstall.lock").write_text(
            tomli_w.dumps(
                {
                    "version": 1,
                    "dependencies": {
                        name: {**definition, "revision": revision * 40}
                        for name, revision in (("ci", "1"), ("formatting", "2"))
                    },
                }
            )
        )
        (project / "unmanaged").write_bytes(b"local content")
        return config

    def test_removes_only_the_named_dependency_and_reports_its_prior_definition(self, config: Path) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        declarations = tomllib.loads(config.read_text())
        recorded = tomllib.loads(lockfile.read_text())

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 0, output
        assert cell_payloads(records) == [
            {
                "content": None,
                "type": "dependency",
                "operation": "dependency remove",
                "dependency": "ci",
                "vcs": "git",
                "repository": "https://example.test/shared.git",
                "ref_kind": "branch",
                "ref": "main",
                "revision": "1" * 40,
                "outcome": "removed",
            }
        ]
        assert tomllib.loads(config.read_text()) == {
            **declarations,
            "dependencies": {"formatting": declarations["dependencies"]["formatting"]},
            "entries": [],
        }
        assert tomllib.loads(lockfile.read_text()) == {
            **recorded,
            "dependencies": {"formatting": recorded["dependencies"]["formatting"]},
            "entries": [],
        }
        assert (config.parent / "unmanaged").read_bytes() == b"local content"
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()

    @pytest.mark.parametrize("state", ["absent", "empty", "missing_dependency"])
    def test_missing_lock_state_allows_removal_without_creating_or_rewriting_a_lock(
        self, config: Path, state: str
    ) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        if state == "absent":
            lockfile.unlink()
        elif state == "empty":
            lockfile.write_text("version = 1\n# Preserve these bytes.\n")
        else:
            recorded = tomllib.loads(lockfile.read_text())
            del recorded["dependencies"]["ci"]
            lockfile.write_text(tomli_w.dumps(recorded) + "# Preserve these bytes.\n")
        before = lockfile.read_bytes() if lockfile.exists() else None

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 0, output
        assert records[0]["revision"] is None
        assert records[0]["ref_kind"] == "branch"
        assert (lockfile.read_bytes() if lockfile.exists() else None) == before
        assert "ci" not in tomllib.loads(config.read_text())["dependencies"]

    @pytest.mark.parametrize("mismatch", ["repository", "selector", "backend"])
    def test_discards_a_stale_lock_without_reporting_its_revision(self, config: Path, mismatch: str) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        if mismatch == "repository":
            recorded["dependencies"]["ci"]["repository"] = "https://example.test/old.git"
        elif mismatch == "selector":
            recorded["dependencies"]["ci"]["selector"] = {"kind": "tag", "value": "main"}
        else:
            recorded["dependencies"]["ci"] = {
                "vcs": "filesystem",
                "repository": "../old-source",
                "revision": "sha256:" + "1" * 64,
            }
        lockfile.write_text(tomli_w.dumps(recorded))

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 0, output
        assert records[0]["revision"] is None
        assert records[0]["vcs"] == "git"
        assert records[0]["repository"] == "https://example.test/shared.git"
        assert records[0]["ref_kind"] == "branch" and records[0]["ref"] == "main"
        assert "ci" not in tomllib.loads(lockfile.read_text())["dependencies"]

    @pytest.mark.parametrize("matching", [False, True])
    def test_omitted_git_selector_reuses_only_a_matching_branch_lock(self, config: Path, matching: bool) -> None:
        declarations = tomllib.loads(config.read_text())
        del declarations["dependencies"]["ci"]["selector"]
        config.write_text(tomli_w.dumps(declarations))
        if not matching:
            lockfile = config.with_name("vcsinstall.lock")
            recorded = tomllib.loads(lockfile.read_text())
            recorded["dependencies"]["ci"]["selector"]["kind"] = "tag"
            lockfile.write_text(tomli_w.dumps(recorded))

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 0, output
        assert records[0]["ref_kind"] == ("branch" if matching else "default")
        assert records[0]["ref"] == ("main" if matching else None)
        assert records[0]["revision"] == ("1" * 40 if matching else None)

    @pytest.mark.parametrize("locked", [False, True])
    def test_normalizes_local_repository_identity_against_the_selected_project(
        self, config: Path, mocker: MockerFixture, locked: bool
    ) -> None:
        declarations = tomllib.loads(config.read_text())
        declarations["dependencies"]["ci"] = {"vcs": "filesystem", "repository": "../unavailable-source"}
        config.write_text(tomli_w.dumps(declarations))
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        repository = config.parent.parent / "unavailable-source"
        recorded["dependencies"].pop("ci")
        if locked:
            recorded["dependencies"]["ci"] = {
                "vcs": "filesystem",
                "repository": str(repository),
                "revision": "sha256:" + "3" * 64,
            }
        lockfile.write_text(tomli_w.dumps(recorded))
        invocation = config.parent / "nested"
        invocation.mkdir()
        mocker.patch.object(Path, "cwd", return_value=invocation)

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 0, output
        assert records[0]["repository"] == str(repository)
        assert records[0]["ref_kind"] == "default" and records[0]["ref"] is None
        assert records[0]["revision"] == ("sha256:" + "3" * 64 if locked else None)
        assert not repository.exists()

    def test_refuses_all_declared_entries_even_when_their_content_or_baselines_are_missing(self, config: Path) -> None:
        declarations = tomllib.loads(config.read_text())
        declarations["entries"] = [
            {"dependency": "ci", "source_path": "@/guide", "destination": path}
            for path in ("@/z-missing", "@/a-local")
        ]
        config.write_text(tomli_w.dumps(declarations))
        (config.parent / "a-local").write_bytes(b"local changes")
        lockfile = config.with_name("vcsinstall.lock")
        before = config.read_bytes(), lockfile.read_bytes()

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 1, output
        assert len(records) == 1 and records[0]["code"] == InvalidArguments.model_fields["code"].default
        assert "ci" in output and "@/a-local, @/z-missing" in output
        assert "Remove or reassign" in output
        assert (config.read_bytes(), lockfile.read_bytes()) == before
        assert (config.parent / "a-local").read_bytes() == b"local changes"

    def test_removes_obsolete_baselines_without_touching_files_or_unrelated_records(self, config: Path) -> None:
        declare_entry(config, source="@/guide", destination="@/obsolete", files=b"old")
        declarations = tomllib.loads(config.read_text())
        declarations["entries"] = [
            {"dependency": "formatting", "source_path": "@/guide", "destination": "@/unselected"}
        ]
        config.write_text(tomli_w.dumps(declarations))
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        unselected = {**recorded["entries"][0], "dependency": "formatting", "destination": "@/unselected"}
        recorded["entries"].append(unselected)
        lockfile.write_text(tomli_w.dumps(recorded))
        (config.parent / "obsolete").write_bytes(b"locally modified old installation")
        (config.parent / "unselected").write_bytes(b"unselected local changes")

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 0, output
        assert len(records) == 1
        assert tomllib.loads(config.read_text())["entries"] == declarations["entries"]
        assert tomllib.loads(lockfile.read_text())["entries"] == [unselected]
        assert (config.parent / "obsolete").read_bytes() == b"locally modified old installation"
        assert (config.parent / "unselected").read_bytes() == b"unselected local changes"

    def test_preserves_a_baseline_still_protecting_a_reassigned_entry(self, config: Path) -> None:
        declare_entry(config, source="@/guide", destination="@/installed", files=b"old")
        declarations = tomllib.loads(config.read_text())
        declarations["entries"][0]["dependency"] = "formatting"
        config.write_text(tomli_w.dumps(declarations))
        lockfile = config.with_name("vcsinstall.lock")
        before = config.read_bytes(), lockfile.read_bytes()

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 3, output
        assert records[0]["code"] == workspace_errors.BaselineMismatch.model_fields["code"].default
        assert records[0]["destination"] == "@/installed"
        assert records[0]["dependency"] == "formatting"
        assert (config.read_bytes(), lockfile.read_bytes()) == before

    def test_last_dependency_leaves_valid_empty_project_files(self, config: Path) -> None:
        assert invoke_dependency_remove(config, "ci")[0] == 0

        code, records, output = invoke_dependency_remove(config, "formatting")

        assert code == 0, output
        assert records[0]["dependency"] == "formatting"
        for path in (config, config.with_name("vcsinstall.lock")):
            assert tomllib.loads(path.read_text()) == {"version": 1, "dependencies": {}, "entries": []}

    def test_unknown_name_preserves_project_files(self, config: Path) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        before = config.read_bytes(), lockfile.read_bytes()

        code, records, output = invoke_dependency_remove(config, "unknown")

        assert code == 1, output
        assert records[0]["code"] == InvalidArguments.model_fields["code"].default
        assert "unknown" in output
        assert (config.read_bytes(), lockfile.read_bytes()) == before

    @pytest.mark.parametrize(
        "arguments", [[], ["ci", "formatting"], ["--force", "ci"], ["--keep-files", "ci"], ["Bad"], ["ci/name"]]
    )
    def test_invalid_command_shape_precedes_project_loading(self, tmp_path: Path, arguments: list[str]) -> None:
        config = tmp_path / "missing.toml"

        result = CliRunner().invoke(application.app, ["--config", str(config), "dependency", "remove", *arguments])

        assert result.exit_code == 2, result.output
        assert not config.exists()

    @pytest.mark.parametrize("filename", ["custom.toml", "vcsinstall.lock"])
    @pytest.mark.parametrize("content", ["invalid[", "version = 2\n"])
    def test_invalid_existing_metadata_is_never_bypassed(self, config: Path, filename: str, content: str) -> None:
        (config.parent / filename).write_text(content)
        lockfile = config.with_name("vcsinstall.lock")
        before = config.read_bytes(), lockfile.read_bytes()

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 2, output
        assert len(records) == 1 and records[0]["type"] == "error"
        assert records[0]["path"] == str(config.parent / filename)
        assert (config.read_bytes(), lockfile.read_bytes()) == before

    def test_source_content_and_cache_access_are_unused(self, config: Path, mocker: MockerFixture) -> None:
        for name in (
            "vcsinstall.cli.commands.dependency.resolve_cache_root",
            "vcsinstall.cli.commands.dependency.get_hosting",
            "vcsinstall.vcs_clients.GitClient.run",
            "vcsinstall.cache.paths.user_cache_path",
            "vcsinstall.filesystem.FSClient.inspect_tree",
            "vcsinstall.filesystem.FSClient.remove",
        ):
            mocker.patch(name, side_effect=AssertionError("unexpected source, content, or cache access"))
        cache = config.parent / "cache"
        cache.mkdir()
        cached_repository = cache / "repository"
        cached_repository.write_bytes(b"retained cache")

        result = CliRunner().invoke(
            application.app,
            ["--config", str(config), "--cache-dir", str(config / "invalid-cache"), "dependency", "remove", "ci"],
        )

        assert result.exit_code == 0, result.output
        assert cached_repository.read_bytes() == b"retained cache"

    @pytest.mark.parametrize("failed_file", ["custom.toml", "vcsinstall.lock"])
    def test_write_failures_report_the_path_and_completed_effects(
        self, config: Path, mocker: MockerFixture, failed_file: str
    ) -> None:
        original = Path.write_bytes
        failure = config.parent / failed_file
        lockfile = config.with_name("vcsinstall.lock")
        before = config.read_bytes(), lockfile.read_bytes()

        def write(path: Path, data: bytes) -> int:
            if path == failure:
                raise PermissionError("project write denied")
            return original(path, data)

        mocker.patch.object(Path, "write_bytes", autospec=True, side_effect=write)

        code, records, output = invoke_dependency_remove(config, "ci")

        assert code == 3, output
        assert len(records) == 1
        assert records[0]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert records[0]["path"] == str(failure)
        assert "completed project writes" in output
        assert lockfile.read_bytes() == before[1]
        assert (config.read_bytes() == before[0]) == (failed_file == "custom.toml")
        assert (config.parent / "unmanaged").read_bytes() == b"local content"
        assert not active_workspace.is_set()

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_success_and_expected_failure_use_shared_protocol(self, config: Path, protocol: str) -> None:
        arguments = ["-p", protocol, "--config", str(config), "dependency", "remove", "ci"]

        success = CliRunner().invoke(application.app, arguments)
        unknown = CliRunner().invoke(application.app, arguments)

        assert success.exit_code == 0, success.output
        assert unknown.exit_code == 1, unknown.output
        assert not success.stderr and not unknown.stderr
        assert "ci" in success.stdout
        if protocol == "llm":
            assert "--VCSINSTALL-CELL " in success.stdout
            assert "--VCSINSTALL-CELL " in unknown.stdout
        elif protocol == "automation":
            assert json.loads(success.stdout)["operation"] == "dependency remove"
            assert json.loads(unknown.stdout)["type"] == "error"

    @pytest.mark.parametrize("help_option", ["--help", "-h"])
    def test_help_requires_no_project_or_external_access(
        self, config: Path, mocker: MockerFixture, help_option: str
    ) -> None:
        load = mocker.patch.object(dependency, "load_workspace", side_effect=AssertionError("unexpected loading"))

        result = CliRunner().invoke(
            application.app, ["--config", str(config / "invalid"), "dependency", "remove", help_option]
        )

        assert result.exit_code == 0, result.output
        assert "NAME" in result.stdout
        load.assert_not_called()
