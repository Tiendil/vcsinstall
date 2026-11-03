import pytest
from pytest_mock import MockerFixture


@pytest.fixture(autouse=True)
def cache_lock_timeout(mocker: MockerFixture) -> None:
    """Limit cache-lock acquisition to one second throughout the test suite."""
    mocker.patch("vcsinstall.cache.storage.LOCK_TIMEOUT_SECONDS", 1)
