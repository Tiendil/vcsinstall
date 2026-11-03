import pytest
from llm_tool_cli.paths import normalize_project_path_id

from vcsinstall.core.paths import contains_path, join_path


class TestJoinPath:
    @pytest.mark.parametrize(
        "root,relative,expected",
        [
            ("@/", "@/", "@/"),
            ("@/", "@/docs/guide", "@/docs/guide"),
            ("@/docs", "@/", "@/docs"),
            ("@/docs", "@/guide", "@/docs/guide"),
            ("@/docs", "@/nested/guide", "@/docs/nested/guide"),
            ("@/docs[*]", "@/space ü\tname", "@/docs[*]/space ü\tname"),
            ("@/docs", "@/literal@/guide", "@/docs/literal@/guide"),
        ],
    )
    def test_roots_and_literal_segments(self, root: str, relative: str, expected: str) -> None:
        result = join_path(normalize_project_path_id(root).unwrap(), normalize_project_path_id(relative).unwrap())

        assert result == expected


class TestContainsPath:
    @pytest.mark.parametrize(
        "root,path,expected",
        [
            ("@/", "@/", True),
            ("@/", "@/docs/guide", True),
            ("@/docs", "@/", False),
            ("@/docs", "@/docs", True),
            ("@/docs", "@/docs/nested/guide", True),
            ("@/docs", "@/docs-old/guide", False),
            ("@/docs", "@/other/docs", False),
            ("@/docs[*]", "@/docs[*]/space ü\tname", True),
            ("@/docs[*]", "@/docs-a/guide", False),
        ],
    )
    def test_segment_boundaries_and_literal_names(self, root: str, path: str, expected: bool) -> None:
        result = contains_path(normalize_project_path_id(root).unwrap(), normalize_project_path_id(path).unwrap())

        assert result == expected
