from collections.abc import Iterator

import pytest
from llm_tool_cli.core.result import Ok, Result
from llm_tool_cli.core.tests.fixtures import isolated_settings
from llm_tool_cli.output import StreamOutputEmitter, output
from llm_tool_cli.protocol.logic_cells.base import LogicCell

__all__ = ["isolated_settings"]


class CollectingEmitter(StreamOutputEmitter):
    def __init__(self) -> None:
        super().__init__()
        self.cells: list[LogicCell] = []

    def emit(self, cells: list[LogicCell]) -> Result[None]:
        self.cells.extend(cells)
        return Ok(None)


@pytest.fixture
def emitter() -> Iterator[CollectingEmitter]:
    selected = CollectingEmitter()
    with output.scope(selected):
        yield selected
