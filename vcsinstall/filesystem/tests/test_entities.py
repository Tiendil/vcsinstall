import pydantic
import pytest
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentKind
from vcsinstall.filesystem import ContentTree, FileInfo, hashes


class TestContentTree:
    @pytest.mark.parametrize(
        "kind,paths",
        [
            (ContentKind.file, ()),
            (ContentKind.file, ("@/child",)),
            (ContentKind.absent, ("@/child",)),
            (ContentKind.directory, ("@/",)),
            (ContentKind.directory, ("@/a", "@/a/b")),
            (ContentKind.directory, ("@/a", "@/a ")),
        ],
    )
    def test_rejects_impossible_or_normalization_colliding_file_trees(
        self, kind: ContentKind, paths: tuple[str, ...]
    ) -> None:
        files = tuple(FileInfo(path=ProjectPathId(path), hash=hashes.hash_file(b"content")) for path in paths)
        with pytest.raises(pydantic.ValidationError):
            ContentTree(kind=kind, files=files)
