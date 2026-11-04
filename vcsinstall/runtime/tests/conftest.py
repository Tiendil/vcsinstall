from collections.abc import Iterator

import pytest
from llm_tool_cli.core.tests.fixtures import isolated_settings
from llm_tool_cli.output import output
from llm_tool_cli.output.tests.helpers import CollectingEmitter

__all__ = ["isolated_settings"]


@pytest.fixture
def emitter() -> Iterator[CollectingEmitter]:
    selected = CollectingEmitter()
    with output.scope(selected):
        yield selected
