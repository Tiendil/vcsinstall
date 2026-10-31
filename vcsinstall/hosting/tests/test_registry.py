from pathlib import Path

from llm_tool_cli.cli.errors import InvalidArguments

from vcsinstall.cache import CacheRootPath, active_cache_root
from vcsinstall.domain import VcsId
from vcsinstall.hosting import FilesystemHostingAdapter, GitHostingAdapter, get_hosting


class TestGetHosting:
    def test_git_factories_bind_the_current_cache_scope(self, tmp_path: Path) -> None:
        first_root = CacheRootPath(tmp_path / "first")
        second_root = CacheRootPath(tmp_path / "second")

        with active_cache_root.scope(first_root):
            first = get_hosting(VcsId.git).unwrap()
            with active_cache_root.scope(second_root):
                second = get_hosting(VcsId.git).unwrap()
            restored = get_hosting(VcsId.git).unwrap()

        assert isinstance(first, GitHostingAdapter)
        assert isinstance(second, GitHostingAdapter)
        assert isinstance(restored, GitHostingAdapter)
        assert first.root == first_root
        assert second.root == second_root
        assert restored.root == first_root
        assert restored is not first
        assert not first_root.exists()
        assert not second_root.exists()

    def test_filesystem_does_not_require_a_cache_scope(self) -> None:
        assert not active_cache_root.is_set()

        assert isinstance(get_hosting(VcsId.filesystem).unwrap(), FilesystemHostingAdapter)

    def test_noop_requires_an_explicit_controlled_adapter(self) -> None:
        assert not active_cache_root.is_set()

        assert get_hosting(VcsId.noop).is_err(InvalidArguments)
