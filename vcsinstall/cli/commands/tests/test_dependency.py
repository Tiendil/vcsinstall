import json
import tomllib
from contextlib import AbstractContextManager
from pathlib import Path
from urllib.parse import quote

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err, Ok, Result
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cache import CacheRootPath, active_cache_root
from vcsinstall.cache import errors as cache_errors
from vcsinstall.cli import application
from vcsinstall.cli.commands import dependency
from vcsinstall.domain import Dependency, DependencyName, RepositoryLocator, VcsId
from vcsinstall.hosting import HostingAdapter
from vcsinstall.sources import DEFAULT_ADAPTERS, SourceAdapter
from vcsinstall.sources import errors as source_errors
from vcsinstall.sources import parse_repository
from vcsinstall.sources.adapters.filesystem import FilesystemSourceAdapter
from vcsinstall.sources.adapters.noop import NoopSourceAdapter
from vcsinstall.vcs_clients import errors as vcs_errors
from vcsinstall.workspace import active_workspace
from vcsinstall.workspace import errors as workspace_errors


class ProbingHosting:
    def __init__(self, result: Result[bool] | None = None) -> None:
        self.result = result if result is not None else Ok(True)
        self.probes: list[RepositoryLocator] = []

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        self.probes.append(repository)
        return self.result

    def acquire(self, dependency: Dependency) -> AbstractContextManager[Result[Path]]:
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
    def test_adapter_diagnostics_reach_cli(self, tmp_path: Path, filename: str) -> None:
        config = tmp_path / "custom.toml"
        config.write_text("version = 1\n")
        text = (
            'version = 1\n[dependencies.ci]\nvcs = "git"\nrepository = "unused-source"\n'
            'selector = { kind = "branch", value = "bad..branch" }\n'
        )
        if filename.endswith("lock"):
            text += 'revision = "' + "1" * 40 + '"\n'
        (tmp_path / filename).write_text(text)

        result = CliRunner().invoke(
            application.app, ["-p", "automation", "--config", str(config), "dependency", "add", "ci", "unused-source"]
        )

        assert result.exit_code == 2
        assert not result.stderr
        record = json.loads(result.stdout)
        assert record["code"] == workspace_errors.DependencyValidation.model_fields["code"].default
        assert record["path"] == str(tmp_path / filename)
        assert record["dependency"] == "ci"
        assert record["source_code"] == source_errors.InvalidGitReference.model_fields["code"].default
        assert record["source_field"] == "branch"
        assert record["source_value"] == "bad..branch"
