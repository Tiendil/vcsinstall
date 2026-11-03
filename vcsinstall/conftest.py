from pathlib import Path

import pytest
from pytest_mock import MockerFixture


@pytest.fixture(autouse=True)
def cache_lock_timeout(mocker: MockerFixture) -> None:
    """Limit cache-lock acquisition to one second throughout the test suite."""
    mocker.patch("vcsinstall.cache.storage.LOCK_TIMEOUT_SECONDS", 1)


@pytest.fixture(autouse=True)
def project_state_root(tmp_path_factory: pytest.TempPathFactory, mocker: MockerFixture) -> Path:
    """Keep project coordination in test-owned storage, including child CLI processes."""
    root = tmp_path_factory.mktemp("state")
    mocker.patch.dict("os.environ", {"XDG_STATE_HOME": str(root)})
    mocker.patch("vcsinstall.workspace.coordination.LOCK_TIMEOUT_SECONDS", 1)
    return root / "vcsinstall"
