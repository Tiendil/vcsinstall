import operator

import pytest

from vcsinstall.domain import VcsId
from vcsinstall.domain.tests import make
from vcsinstall.sources import DEFAULT_ADAPTERS, get_adapter
from vcsinstall.sources.tests.helpers import RecordingAdapter


class TestGetAdapter:
    @pytest.mark.parametrize("vcs", VcsId)
    def test_selects_backend_specific_validation(self, vcs: VcsId) -> None:
        adapter = get_adapter(vcs)

        result = adapter.validate_dependency(make.dependency(vcs=vcs, branch="main"))

        assert result.is_ok() == (vcs != VcsId.filesystem)

    def test_default_registry_is_read_only(self) -> None:
        with pytest.raises(TypeError):
            # Intentionally violate the read-only interface to verify runtime rejection.
            operator.setitem(DEFAULT_ADAPTERS, VcsId.noop, RecordingAdapter())  # type: ignore[call-overload]

    def test_explicit_registry_replaces_adapter_without_changing_defaults(self) -> None:
        default = get_adapter(VcsId.noop)
        adapter = RecordingAdapter()

        assert get_adapter(VcsId.noop, adapters={**DEFAULT_ADAPTERS, VcsId.noop: adapter}) is adapter
        assert get_adapter(VcsId.noop) is default
