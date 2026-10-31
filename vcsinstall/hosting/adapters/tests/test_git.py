from pathlib import Path

import pytest
from filelock import FileLock, Timeout
from llm_tool_cli.core.result import Err, Ok
from pytest_mock import MockerFixture

from vcsinstall.cache import CacheRootPath
from vcsinstall.domain import RepositoryLocator, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.hosting import GitHostingAdapter
from vcsinstall.vcs_clients import GitClient, errors
from vcsinstall.vcs_clients.tests.helpers import create_repository


class TestGitHostingAdapter:
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
