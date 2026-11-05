from pathlib import Path

import pytest
from filelock import FileLock, Timeout
from llm_tool_cli.core.result import Err, Ok
from llm_tool_cli.paths import CacheRootPath
from pytest_mock import MockerFixture

from vcsinstall.domain import RepositoryLocator, SourceRevision, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.hosting import GitHostingAdapter
from vcsinstall.vcs_clients import GitClient, errors
from vcsinstall.vcs_clients.tests.helpers import create_repository


class TestGitHostingAdapter:
    def test_acquire__shares_selectors_and_separates_repository_identities(self, tmp_path: Path) -> None:
        first = create_repository(tmp_path / "first" / "shared")
        second = create_repository(tmp_path / "second" / "shared")
        dependency = make.dependency(repository=str(first.path), selector=make.selector("branch", "trunk"))
        hosting = GitHostingAdapter(root=CacheRootPath(tmp_path / "cache"))

        with hosting.acquire(dependency) as acquired:
            first_path = acquired.unwrap()
        pinned = dependency.replace(selector=make.selector("revision", first.resolve_commit("HEAD").unwrap()))
        with hosting.acquire(pinned) as acquired:
            assert acquired.unwrap() == first_path
        with hosting.acquire(dependency.replace(repository=str(second.path))) as acquired:
            assert acquired.unwrap() != first_path

    @pytest.mark.parametrize("selector_kind", [None, "branch", "tag"])
    def test_acquire__reuses_locked_revision_without_refreshing_binding(
        self, tmp_path: Path, selector_kind: str | None
    ) -> None:
        client = create_repository(tmp_path / "source")
        revision = client.resolve_commit("HEAD").unwrap()
        selector = make.selector(selector_kind, "release") if selector_kind else None
        dependency = make.dependency(repository=str(client.path), selector=selector)
        hosting = GitHostingAdapter(root=CacheRootPath(tmp_path / "cache"))
        with hosting.acquire(dependency) as acquired:
            acquired.unwrap()
        client.path.rename(tmp_path / "unavailable-source")

        with hosting.acquire(dependency, revision=SourceRevision(revision), refresh=False) as acquired:
            cached = GitClient(path=acquired.unwrap(), repository=dependency.repository)
            assert cached.resolve_commit(revision).unwrap() == revision

    def test_acquire__fetches_missing_locked_revision_without_refreshing_binding(self, tmp_path: Path) -> None:
        client = create_repository(tmp_path / "source")
        initial = client.resolve_commit("HEAD").unwrap()
        dependency = make.dependency(repository=client.path.as_uri(), selector=make.selector("branch", "trunk"))
        hosting = GitHostingAdapter(root=CacheRootPath(tmp_path / "cache"))
        with hosting.acquire(dependency) as acquired:
            acquired.unwrap()
        (client.path / "guide.txt").write_bytes(b"locked content")
        revision = client.commit("Advance source", all_tracked=True).unwrap()

        with hosting.acquire(dependency, revision=SourceRevision(revision), refresh=False) as acquired:
            cached = GitClient(path=acquired.unwrap(), repository=dependency.repository)
            assert cached.resolve_commit(revision).unwrap() == revision
            assert cached.resolve_commit("refs/heads/trunk").unwrap() == initial

    def test_acquire__fetches_required_unreferenced_revision_while_refreshing_selector(self, tmp_path: Path) -> None:
        client = create_repository(tmp_path / "source")
        client.create_branch("detached-history", checkout=True).unwrap()
        (client.path / "guide.txt").write_bytes(b"locked content")
        locked = client.commit("Historical lock", all_tracked=True).unwrap()
        client.run(["checkout", "trunk", "--"]).unwrap()
        client.run(["branch", "-D", "detached-history"]).unwrap()
        dependency = make.dependency(repository=client.path.as_uri(), selector=make.selector("branch", "trunk"))
        hosting = GitHostingAdapter(root=CacheRootPath(tmp_path / "cache"))

        with hosting.acquire(dependency, revision=SourceRevision(locked)) as acquired:
            cached = GitClient(path=acquired.unwrap(), repository=dependency.repository)
            assert cached.resolve_commit(locked).unwrap() == locked
            assert cached.resolve_commit("refs/heads/trunk").unwrap() != locked

    def test_acquire__required_revision_failure_preserves_original_error(self, tmp_path: Path) -> None:
        client = create_repository(tmp_path / "source")
        dependency = make.dependency(repository=str(client.path), selector=make.selector("branch", "trunk"))
        hosting = GitHostingAdapter(root=CacheRootPath(tmp_path / "cache"))

        with hosting.acquire(dependency, revision=SourceRevision("0" * 40)) as acquired:
            assert acquired.is_err(errors.CommandFailed)

    @pytest.mark.parametrize("missing", [False, True])
    def test_acquire__holds_cache_lock_until_scope_exit(self, tmp_path: Path, missing: bool) -> None:
        source = tmp_path / "source"
        if not missing:
            create_repository(source)
        dependency = make.dependency(repository=str(source))
        root = CacheRootPath(tmp_path / "cache")
        hosting = GitHostingAdapter(root=root)
        lock = FileLock(root / "cache.lock", timeout=0, preserve_lock_file=True, fallback_to_soft=False)

        with hosting.acquire(dependency) as acquired:
            if missing:
                assert acquired.is_err(errors.CommandFailed)
            else:
                client = GitClient(path=acquired.unwrap(), repository=dependency.repository)
                assert client.is_repository().unwrap()
            with pytest.raises(Timeout):
                lock.acquire()

        with lock:
            pass

    def test_acquire__releases_lock_when_consumer_raises(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        create_repository(source)
        dependency = make.dependency(repository=str(source))
        root = CacheRootPath(tmp_path / "cache")
        hosting = GitHostingAdapter(root=root)

        with pytest.raises(ValueError, match="consumer failed"):
            with hosting.acquire(dependency) as acquired:
                acquired.unwrap()
                raise ValueError("consumer failed")

        with FileLock(root / "cache.lock", timeout=0, preserve_lock_file=True, fallback_to_soft=False):
            pass

    @pytest.mark.parametrize(
        "object_format,prefix_length", [("sha1", 12), ("sha1", 40), ("sha256", 12), ("sha256", 40), ("sha256", 64)]
    )
    def test_acquire__retrieves_missing_revision_from_populated_cache(
        self, tmp_path: Path, object_format: str, prefix_length: int
    ) -> None:
        client = create_repository(tmp_path / "source", object_format=object_format)
        dependency = make.dependency(repository=str(client.path))
        hosting = GitHostingAdapter(root=CacheRootPath(tmp_path / "cache"))
        with hosting.acquire(dependency) as acquired:
            acquired.unwrap()
        (client.path / "guide.txt").write_bytes(b"new commit")
        revision = client.commit("Advance source", all_tracked=True).unwrap()
        pinned = dependency.replace(selector=make.selector("revision", revision[:prefix_length]))

        with hosting.acquire(pinned) as acquired:
            cached = GitClient(path=acquired.unwrap(), repository=dependency.repository)
            assert cached.resolve_commit(revision[:prefix_length]).unwrap() == revision

    @pytest.mark.parametrize("abbreviated", [False, True])
    def test_acquire__reuses_cached_pin_without_source_access(self, tmp_path: Path, abbreviated: bool) -> None:
        client = create_repository(tmp_path / "source")
        dependency = make.dependency(repository=str(client.path))
        revision = client.resolve_commit("HEAD").unwrap()
        hosting = GitHostingAdapter(root=CacheRootPath(tmp_path / "cache"))
        with hosting.acquire(dependency) as acquired:
            acquired.unwrap()
        client.path.rename(tmp_path / "unavailable-source")
        value = revision[:12] if abbreviated else revision
        pinned = dependency.replace(selector=make.selector("revision", value))

        with hosting.acquire(pinned) as acquired:
            cached = GitClient(path=acquired.unwrap(), repository=dependency.repository)
            assert cached.resolve_commit(value).unwrap() == revision

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "https://[invalid",
            "://missing",
            "file://server/shared",
            "file:///source?query=value",
            "filesystem+file:///source",
        ],
    )
    def test_probe_repository__malformed_values_are_non_matches(self, mocker: MockerFixture, value: str) -> None:
        native = mocker.patch.object(GitClient, "probe_remote", return_value=Ok(True))

        assert not GitHostingAdapter.probe_repository(RepositoryLocator(value)).unwrap()

        native.assert_not_called()

    def test_probe_repository__accepts_native_repository_metadata(self, tmp_path: Path) -> None:
        client = create_repository(tmp_path / "source")

        assert GitHostingAdapter.probe_repository(RepositoryLocator(client.path.as_uri())).unwrap()

    def test_probe_repository__preserves_operational_failures(self, mocker: MockerFixture) -> None:
        repository = RepositoryLocator("https://example.test/shared")
        problem = errors.CommandFailed(vcs=VcsId.git, repository=repository, reason="Permission denied")
        mocker.patch.object(GitClient, "probe_remote", return_value=Err([problem]))

        assert GitHostingAdapter.probe_repository(repository).unwrap_err() == [problem]

    def test_probe_repository__internal_failures_propagate(self, mocker: MockerFixture) -> None:
        cause = errors.InternalError("Broken native probe")
        mocker.patch.object(GitClient, "probe_remote", side_effect=cause)

        with pytest.raises(errors.InternalError) as raised:
            GitHostingAdapter.probe_repository(RepositoryLocator("https://example.test/shared"))

        assert raised.value is cause
