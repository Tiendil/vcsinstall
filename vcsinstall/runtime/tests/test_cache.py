from pathlib import Path

from llm_tool_cli.core.result import Err
from llm_tool_cli.output.tests.helpers import CollectingEmitter
from pytest_mock import MockerFixture

from vcsinstall.cache import CacheRootPath, active_cache_root, errors
from vcsinstall.cache.tests.helpers import cached_repository
from vcsinstall.runtime import cache_clean


class TestCacheClean:
    def test_emits_one_result_for_each_invocation(self, tmp_path: Path, emitter: CollectingEmitter) -> None:
        root = CacheRootPath(tmp_path / "cache")
        cached_repository(root)

        with active_cache_root.scope(root):
            cache_clean().unwrap()
            cache_clean().unwrap()

        records = [cell.render_automation()[0] for cell in emitter.cells]
        assert [cell.meta for cell in records] == [
            {"type": "cache", "operation": "clean", "cache_dir": str(root), "outcome": outcome}
            for outcome in ("cleaned", "unchanged")
        ]
        assert all(cell.content is None for cell in records)

    def test_propagates_errors_without_emitting_success(
        self, tmp_path: Path, emitter: CollectingEmitter, mocker: MockerFixture
    ) -> None:
        root = CacheRootPath(tmp_path)
        cause = PermissionError("unreadable cache")
        problem = errors.AccessFailed(path=root, reason=str(cause)).with_cause(cause)
        mocker.patch("vcsinstall.runtime.cache.clean_cache", return_value=Err([problem]))

        with active_cache_root.scope(root):
            failures = cache_clean().unwrap_err()

        assert failures == [problem]
        assert failures[0].cause is cause
        assert not emitter.cells
