from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from vcsinstall.cache import CacheRootPath, RepoCache, errors
from vcsinstall.cache.tests.helpers import held_lock
from vcsinstall.domain import Dependency, RepositoryLocator, VcsId
from vcsinstall.domain.tests import make


class TestRepoCache:
    def test_lock__nested_repositories_keep_exclusive_access_until_outer_scope_exits(self, tmp_path: Path) -> None:
        first = make.dependency(repository="https://host/first")
        second = make.dependency(repository="https://host/second")
        external = FileLock(tmp_path / "cache.lock", timeout=0, preserve_lock_file=True)
        with RepoCache(root=CacheRootPath(tmp_path), dependency=first).lock() as outer:
            outer_path = outer.unwrap()
            with RepoCache(root=CacheRootPath(tmp_path), dependency=second).lock() as inner:
                assert inner.unwrap() != outer_path
                with pytest.raises(Timeout):
                    external.acquire()
            with pytest.raises(Timeout):
                external.acquire()
        with external:
            pass

    def test_lock__identity_and_release(self, tmp_path: Path) -> None:
        dependency = Dependency(vcs=VcsId.git, repository=RepositoryLocator("https://host/team/repo"))
        cache = tmp_path / "cache"
        with RepoCache(root=CacheRootPath(cache), dependency=dependency).lock() as result:
            first = result.unwrap()
            assert first.is_relative_to(cache / "repos" / "git")
        with RepoCache(
            root=CacheRootPath(cache), dependency=dependency.replace(selector=make.selector("branch", "other"))
        ).lock() as result:
            assert result.unwrap() == first
        with RepoCache(
            root=CacheRootPath(cache), dependency=dependency.replace(repository="https://host/another/repo")
        ).lock() as result:
            assert result.unwrap() != first
        assert (cache / "cache.lock").is_file()
        with FileLock(cache / "cache.lock", timeout=0, preserve_lock_file=True):
            pass

    def test_lock__timeout_preserves_cache(self, tmp_path: Path) -> None:
        dependency = Dependency(vcs=VcsId.git, repository=RepositoryLocator("https://host/repo"))
        cache = RepoCache(root=CacheRootPath(tmp_path), dependency=dependency)
        with cache.lock() as result:
            entry = result.unwrap()
        before = {path: path.read_bytes() for path in entry.iterdir()}
        with held_lock(tmp_path / "cache.lock"):
            with cache.lock() as blocked:
                problem = blocked.unwrap_err()[0]
                assert isinstance(problem, errors.LockTimeout)
                assert problem.path == tmp_path
            assert {path: path.read_bytes() for path in entry.iterdir()} == before
        with cache.lock() as result:
            assert result.unwrap() == entry

    def test_lock__conflicting_ownership_fails_without_changes(self, tmp_path: Path) -> None:
        dependency = Dependency(vcs=VcsId.git, repository=RepositoryLocator("https://host/repo"))
        cache = RepoCache(root=CacheRootPath(tmp_path), dependency=dependency)
        with cache.lock() as result:
            entry = result.unwrap()
        identity = (entry / "identity.json").read_text()
        (entry / "identity.json").write_text("unrelated data")
        with cache.lock() as result:
            assert isinstance(result.unwrap_err()[0], errors.AccessFailed)
        assert (entry / "identity.json").read_text() == "unrelated data"
        (entry / "identity.json").write_text(identity)
        with cache.lock() as result:
            assert result.unwrap() == entry

    def test_lock__exception_releases_lock(self, tmp_path: Path) -> None:
        dependency = Dependency(vcs=VcsId.git, repository=RepositoryLocator("https://host/repo"))
        cache = RepoCache(root=CacheRootPath(tmp_path), dependency=dependency)
        try:
            with cache.lock() as result:
                assert result.is_ok()
                raise ValueError("caller failure")
        except ValueError:
            pass
        with cache.lock() as result:
            assert result.is_ok()

    def test_lock__different_roots_have_independent_locks(self, tmp_path: Path) -> None:
        dependency = Dependency(vcs=VcsId.git, repository=RepositoryLocator("https://host/repo"))
        with held_lock(tmp_path / "cache.lock"):
            with RepoCache(root=CacheRootPath(tmp_path / "other"), dependency=dependency).lock() as result:
                assert result.is_ok()

    def test_lock__unusable_root_preserves_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "cache"
        path.write_bytes(b"unrelated")
        dependency = Dependency(vcs=VcsId.git, repository=RepositoryLocator("https://host/repo"))
        with RepoCache(root=CacheRootPath(path), dependency=dependency).lock() as result:
            assert isinstance(result.unwrap_err()[0], errors.AccessFailed)
        assert path.read_bytes() == b"unrelated"
