from pathlib import Path

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config.runtime import RuntimeConfig, runtime_config
from llm_tool_cli.paths import CacheRootPath
from llm_tool_cli.protocol import Protocol

from vcsinstall.domain.tests import make
from vcsinstall.hosting import FilesystemHostingAdapter, GitHostingAdapter, get_hosting


class TestGetHosting:
    @pytest.mark.parametrize(
        "repository",
        ["https://github.com/team/shared.git", "git@github.com:team/shared.git", "https://example.test/shared"],
    )
    def test_all_git_hosts_use_native_acquisition(self, tmp_path: Path, repository: str) -> None:
        root = CacheRootPath(tmp_path / "cache")
        with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=root)):
            hosting = get_hosting(make.dependency(repository=repository)).unwrap()
        assert isinstance(hosting, GitHostingAdapter)
        assert hosting.root == root
        assert not root.exists()

    def test_factories_bind_the_current_cache_scope(self, tmp_path: Path) -> None:
        first_root = CacheRootPath(tmp_path / "first")
        second_root = CacheRootPath(tmp_path / "second")
        dependency = make.dependency()
        with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=first_root)):
            first = get_hosting(dependency).unwrap()
            with runtime_config.scope(RuntimeConfig(protocol=Protocol.human, cache_dir=second_root)):
                second = get_hosting(dependency).unwrap()
            restored = get_hosting(dependency).unwrap()
        assert isinstance(first, GitHostingAdapter)
        assert isinstance(second, GitHostingAdapter)
        assert isinstance(restored, GitHostingAdapter)
        assert (first.root, second.root, restored.root) == (first_root, second_root, first_root)
        assert restored is not first
        assert not first_root.exists() and not second_root.exists()

    def test_filesystem_does_not_require_cache_settings(self) -> None:
        assert not runtime_config.is_set()
        assert isinstance(get_hosting(make.dependency(vcs="filesystem")).unwrap(), FilesystemHostingAdapter)

    def test_noop_requires_a_controlled_adapter(self) -> None:
        assert get_hosting(make.dependency(vcs="noop")).is_err(InvalidArguments)
