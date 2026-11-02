import shutil
from pathlib import Path

import pytest
from filelock import FileLock, Timeout
from llm_tool_cli.paths import errors as path_errors
from pytest_mock import MockerFixture

from vcsinstall.cache import CacheRootPath, RepoCache, clean_cache, errors
from vcsinstall.cache.tests.helpers import cached_repository, held_lock
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


class TestCleanCache:
    def test_missing_root_does_not_create_directories_or_lock(self, tmp_path: Path) -> None:
        root = CacheRootPath(tmp_path / "absent" / "cache")

        assert not clean_cache(root).unwrap()
        assert not root.parent.exists()

    def test_empty_cache_keeps_the_same_lock_file_on_repeated_cleanup(self, tmp_path: Path) -> None:
        root = CacheRootPath(tmp_path)

        assert not clean_cache(root).unwrap()
        inode = (root / "cache.lock").stat().st_ino
        assert not clean_cache(root).unwrap()
        assert (root / "cache.lock").stat().st_ino == inode
        assert list(root.iterdir()) == [root / "cache.lock"]

    def test_removes_all_owned_repositories_and_versions_preserving_unrelated_data(self, tmp_path: Path) -> None:
        root = CacheRootPath(tmp_path / "cache")
        entries = [cached_repository(root, vcs=vcs) for vcs in ("git", "filesystem", "noop")]
        unrelated = [root / "notes", root / "repos" / "notes", root / "repos" / "git" / "unmanaged"]
        for path in unrelated:
            path.write_bytes(b"unrelated content")
        inode = (root / "cache.lock").stat().st_ino

        assert clean_cache(root).unwrap()

        assert all(not entry.exists() for entry in entries)
        assert all(path.read_bytes() == b"unrelated content" for path in unrelated)
        assert (root / "cache.lock").stat().st_ino == inode
        assert not clean_cache(root).unwrap()
        with FileLock(root / "cache.lock", timeout=0, preserve_lock_file=True):
            pass

    @pytest.mark.parametrize(
        "marker_state", ["missing", "text", "utf8", "object", "backend", "repository", "symlink", "directory"]
    )
    def test_unverified_ownership_preserves_existing_data(self, tmp_path: Path, marker_state: str) -> None:
        root = CacheRootPath(tmp_path / "cache")
        entry = cached_repository(root)
        marker = entry / "identity.json"
        original = marker.read_bytes()
        marker.unlink()
        outside = tmp_path / "outside"
        outside.write_bytes(original)
        replacements = {
            "text": b"unrelated data",
            "utf8": b"\xff",
            "object": b'{"git": "source"}',
            "backend": b'["filesystem", "https://host/shared"]',
            "repository": b'["git", "https://host/another"]',
        }
        if marker_state in replacements:
            marker.write_bytes(replacements[marker_state])
        elif marker_state == "symlink":
            marker.symlink_to(outside)
        elif marker_state == "directory":
            marker.mkdir()

        assert not clean_cache(root).unwrap()

        assert (entry / "repository" / "versions" / "first").read_bytes() == b"cached first revision"
        assert outside.read_bytes() == original
        if marker_state in replacements:
            assert marker.read_bytes() == replacements[marker_state]
        elif marker_state == "symlink":
            assert marker.is_symlink()

    @pytest.mark.parametrize("level", ["repos", "backend", "entry"])
    @pytest.mark.parametrize("kind", ["file", "symlink"])
    def test_unmanaged_cache_layout_is_never_traversed_or_removed(self, tmp_path: Path, level: str, kind: str) -> None:
        root = CacheRootPath(tmp_path / "cache")
        outside = tmp_path / "outside"
        cached_repository(outside)
        path = root / {"repos": "repos", "backend": "repos/git", "entry": "repos/git/unmanaged"}[level]
        path.parent.mkdir(parents=True)
        if kind == "file":
            path.write_bytes(b"unrelated")
        else:
            path.symlink_to(outside, target_is_directory=True)

        assert not clean_cache(root).unwrap()

        assert path.exists()
        assert any((outside / "repos" / "git").iterdir())
        if kind == "file":
            assert path.read_bytes() == b"unrelated"
        else:
            assert path.is_symlink()

    def test_payload_symlinks_are_unlinked_without_touching_their_targets(self, tmp_path: Path) -> None:
        root = CacheRootPath(tmp_path / "cache")
        entry = cached_repository(root)
        outside = tmp_path / "source"
        outside.mkdir()
        (outside / "guide").write_bytes(b"source content")
        (entry / "linked-directory").symlink_to(outside, target_is_directory=True)
        (entry / "repository" / "linked-file").symlink_to(outside / "guide")
        (entry / "dangling-link").symlink_to(tmp_path / "absent")

        assert clean_cache(root).unwrap()

        assert not entry.exists()
        assert (outside / "guide").read_bytes() == b"source content"

    def test_cache_lock_blocks_cleanup_and_preserves_cached_data(self, tmp_path: Path) -> None:
        root = CacheRootPath(tmp_path / "cache")
        entry = cached_repository(root)

        with held_lock(root / "cache.lock"):
            problem = clean_cache(root).unwrap_err()[0]

        assert isinstance(problem, errors.LockTimeout)
        assert problem.path == root
        assert (entry / "repository" / "versions" / "first").exists()
        assert clean_cache(root).unwrap()

    def test_cleanup_holds_the_shared_lock_until_deletion_finishes(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        root = CacheRootPath(tmp_path / "cache")
        cached_repository(root)
        remove = shutil.rmtree

        def check_lock(path: Path) -> None:
            with pytest.raises(Timeout):
                FileLock(root / "cache.lock", timeout=0, preserve_lock_file=True).acquire()
            remove(path)

        mocker.patch("vcsinstall.cache.storage.shutil.rmtree", side_effect=check_lock)

        assert clean_cache(root).unwrap()

        with FileLock(root / "cache.lock", timeout=0, preserve_lock_file=True):
            pass

    def test_inspection_failure_retains_cause_and_releases_lock(self, tmp_path: Path, mocker: MockerFixture) -> None:
        root = CacheRootPath(tmp_path / "cache")
        entry = cached_repository(root)
        cause = PermissionError(13, "Unreadable identity", str(entry / "identity.json"))
        mocker.patch("pathlib.Path.read_text", side_effect=cause)

        problem = clean_cache(root).unwrap_err()[0]

        assert isinstance(problem, errors.AccessFailed)
        assert problem.path == root
        assert problem.cause is cause
        assert (entry / "repository" / "versions" / "first").exists()
        with FileLock(root / "cache.lock", timeout=0, preserve_lock_file=True):
            pass

    def test_failed_deletion_reports_partial_cleanup_and_keeps_ownership_for_retry(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        root = CacheRootPath(tmp_path / "cache")
        entry = cached_repository(root)
        cause = PermissionError(13, "Cannot delete second revision", str(entry / "repository"))

        def fail_after_removing_file(path: Path) -> None:
            (path / "versions" / "first").unlink()
            raise cause

        removal = mocker.patch("vcsinstall.cache.storage.shutil.rmtree", side_effect=fail_after_removing_file)

        problem = clean_cache(root).unwrap_err()[0]

        assert isinstance(problem, errors.CleanupFailed)
        assert problem.path == root
        assert problem.entry == entry
        assert problem.cause is cause
        assert "partially" in problem.format_message()
        assert not (entry / "repository" / "versions" / "first").exists()
        assert (entry / "identity.json").is_file()
        mocker.stop(removal)
        assert clean_cache(root).unwrap()

    def test_regular_file_root_fails_without_removing_it(self, tmp_path: Path) -> None:
        root = CacheRootPath(tmp_path / "cache")
        root.write_bytes(b"unrelated")

        problem = clean_cache(root).unwrap_err()[0]

        assert isinstance(problem, errors.AccessFailed)
        assert problem.path == root
        assert root.read_bytes() == b"unrelated"

    def test_symlinked_lock_is_not_removed_with_disposable_data(self, tmp_path: Path) -> None:
        root = CacheRootPath(tmp_path / "cache")
        entry = cached_repository(root)
        lock_path = root / "cache.lock"
        lock_path.unlink()
        target = entry / "repository" / "versions" / "first"
        lock_path.symlink_to(target)

        problem = clean_cache(root).unwrap_err()[0]

        assert isinstance(problem, path_errors.InvalidProjectPath)
        assert problem.path == str(lock_path)
        assert lock_path.is_symlink()
        assert target.read_bytes() == b"cached first revision"
