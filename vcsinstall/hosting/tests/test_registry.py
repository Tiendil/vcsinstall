from pathlib import Path

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config.runtime import RuntimeConfig, runtime_config
from llm_tool_cli.core.result import Err, Ok
from llm_tool_cli.paths import CacheRootPath
from llm_tool_cli.protocol import Protocol
from pytest_mock import MockerFixture

from vcsinstall.core import errors as core_errors
from vcsinstall.domain.tests import make
from vcsinstall.hosting import (
    FilesystemHostingAdapter,
    GitHostingAdapter,
    GitHubHostingAdapter,
    HostingProvider,
    errors,
    get_hosting,
)
from vcsinstall.hosting.adapters import github
from vcsinstall.vcs_clients.tests.helpers import create_repository


class TestGetHosting:
    @pytest.mark.parametrize("provider", [None, HostingProvider.git, HostingProvider.github])
    def test_git_factories_bind_the_current_cache_scope(
        self, tmp_path: Path, provider: HostingProvider | None
    ) -> None:
        first_root = CacheRootPath(tmp_path / "first")
        second_root = CacheRootPath(tmp_path / "second")
        dependency = make.dependency()

        with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=first_root)):
            first = get_hosting(dependency, provider).unwrap()
            with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=second_root)):
                second = get_hosting(dependency, provider).unwrap()
            restored = get_hosting(dependency, provider).unwrap()

        if provider == HostingProvider.github:
            assert isinstance(first, GitHubHostingAdapter)
            assert isinstance(second, GitHubHostingAdapter)
            assert isinstance(restored, GitHubHostingAdapter)
            first, second, restored = first.git, second.git, restored.git
        assert isinstance(first, GitHostingAdapter)
        assert isinstance(second, GitHostingAdapter)
        assert isinstance(restored, GitHostingAdapter)
        assert first.root == first_root
        assert second.root == second_root
        assert restored.root == first_root
        assert restored is not first
        assert not first_root.exists()
        assert not second_root.exists()

    @pytest.mark.parametrize("provider", [None, HostingProvider.filesystem])
    def test_filesystem_does_not_require_a_cache_scope(self, provider: HostingProvider | None) -> None:
        assert not runtime_config.is_set()

        assert isinstance(get_hosting(make.dependency(vcs="filesystem"), provider).unwrap(), FilesystemHostingAdapter)

    @pytest.mark.parametrize("provider", [None, *HostingProvider])
    def test_noop_requires_an_explicit_controlled_adapter(self, provider: HostingProvider | None) -> None:
        assert not runtime_config.is_set()

        assert get_hosting(make.dependency(vcs="noop"), provider).is_err(InvalidArguments)

    @pytest.mark.parametrize("repository", ["https://github.com/team/shared.git", "git@github.com:team/shared.git"])
    def test_recognized_github_source_selects_github_without_service_access(
        self, tmp_path: Path, repository: str, mocker: MockerFixture
    ) -> None:
        client = mocker.patch.object(github, "_run", side_effect=AssertionError("No service-client access is needed"))
        with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=CacheRootPath(tmp_path / "cache"))):
            hosting = get_hosting(make.dependency(repository=repository)).unwrap()

        assert isinstance(hosting, GitHubHostingAdapter)
        client.assert_not_called()

    @pytest.mark.parametrize("provider", [HostingProvider.git, HostingProvider.github])
    def test_explicit_provider_overrides_detection(
        self, tmp_path: Path, provider: HostingProvider, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "vcsinstall.hosting.registry.infer_github", side_effect=AssertionError("Override bypasses inference")
        )
        with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=CacheRootPath(tmp_path / "cache"))):
            hosting = get_hosting(make.dependency(repository="https://github.com/team/shared.git"), provider).unwrap()

        assert isinstance(hosting, GitHostingAdapter if provider == HostingProvider.git else GitHubHostingAdapter)

    @pytest.mark.parametrize(
        "vcs,provider",
        [
            ("git", HostingProvider.filesystem),
            ("filesystem", HostingProvider.git),
            ("filesystem", HostingProvider.github),
        ],
    )
    def test_incompatible_provider_fails_before_cache_or_service_access(
        self, vcs: str, provider: HostingProvider, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(github, "_run", side_effect=AssertionError("Incompatible selection needs no service"))
        assert not runtime_config.is_set()

        assert get_hosting(make.dependency(vcs=vcs), provider).is_err(InvalidArguments)

    @pytest.mark.parametrize("detection", ["unrecognized", "unavailable", "ambiguous"])
    def test_optional_detection_preserves_native_git(
        self, tmp_path: Path, detection: str, mocker: MockerFixture
    ) -> None:
        repository = "https://git.example.test/team/shared.git"
        if detection == "unavailable":
            mocker.patch.object(
                github,
                "_run",
                return_value=Err([errors.ServiceFailed(host="git.example.test", reason="gh unavailable")]),
            )
        elif detection == "unrecognized":
            mocker.patch.object(github, "_run", return_value=Ok(b"inherited-user\n"))
        else:
            source = create_repository(tmp_path / "source")
            source.set_config("remote.one.url", "https://github.com/team/one.git").unwrap()
            source.set_config("remote.two.url", "https://github.com/team/two.git").unwrap()
            repository = str(source.path)

        with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=CacheRootPath(tmp_path / "cache"))):
            hosting = get_hosting(make.dependency(repository=repository)).unwrap()

        assert isinstance(hosting, GitHostingAdapter)

    def test_local_remote_cycles_do_not_block_native_git(self, tmp_path: Path) -> None:
        source = create_repository(tmp_path / "source")
        source.set_config("remote.origin.url", str(source.path)).unwrap()

        with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=CacheRootPath(tmp_path / "cache"))):
            hosting = get_hosting(make.dependency(repository=str(source.path))).unwrap()

        assert isinstance(hosting, GitHostingAdapter)

    def test_unexpected_detection_errors_remain_exceptions(self, mocker: MockerFixture) -> None:
        mocker.patch.object(github, "_run", side_effect=core_errors.InternalError("Unexpected bug"))

        with pytest.raises(core_errors.InternalError):
            get_hosting(make.dependency(repository="https://git.example.test/team/shared.git"))
