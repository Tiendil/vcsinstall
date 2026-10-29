import pydantic
import pytest

from vcsinstall.cli import DependencySource


class TestDependencySource:
    @pytest.mark.parametrize(
        "value,dependency,path",
        [
            ("ci@/specs", "ci", "@/specs"),
            ("ci@/", "ci", "@/"),
            ("ci_2@/LICENSE", "ci_2", "@/LICENSE"),
            ("shared-tools@/guides/../LICENSE", "shared-tools", "@/LICENSE"),
            ("ci@/guides/..", "ci", "@/"),
        ],
    )
    def test_model_validate__parses_and_normalizes(self, value: str, dependency: str, path: str) -> None:
        source = DependencySource.model_validate(value)

        assert source.dependency == dependency
        assert source.source_path == path

    @pytest.mark.parametrize(
        "path",
        [
            "@/notes%20+draft#1?.md",
            "@/notes%ZZ.md",
            "@/ docs/Заметки.md",
            "@/@/notes",
            "@/folder@/notes",
            "@/~/literal*.md",
        ],
    )
    def test_model_validate__preserves_literal_path_text(self, path: str) -> None:
        source = DependencySource.model_validate("ci" + path)

        assert source.source_path == path

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "ci",
            "ci@",
            "ci@notes",
            "@/",
            "@/notes",
            "Ci@/notes",
            "ci@other@/notes",
            "ci@/..",
            "ci@/../notes",
            "ci@/a/../../notes",
            "ci@/a//notes",
            "ci@/notes/",
            "dependency://ci/notes",
            "ci://notes",
            "ci:@/notes",
            "/absolute/notes",
        ],
    )
    def test_model_validate__rejects_invalid_references(self, value: str) -> None:
        with pytest.raises(pydantic.ValidationError):
            DependencySource.model_validate(value)

    @pytest.mark.parametrize("value,field", [("Ci@/notes", "dependency"), ("ci@/../notes", "source_path")])
    def test_model_validate__retains_field_error_context(self, value: str, field: str) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            DependencySource.model_validate(value)

        failure = caught.value.errors()[0]
        assert failure["loc"] == (field,)
        assert failure["input"] == ("Ci" if field == "dependency" else "@/../notes")

    def test_model_validate_json__round_trips_canonical_reference_fields(self) -> None:
        source = DependencySource.model_validate("ci@/docs/../ notes%20+draft#1?.md ")

        restored = DependencySource.model_validate_json(source.model_dump_json())

        assert restored == source
        assert restored.model_dump() == {"dependency": "ci", "source_path": "@/ notes%20+draft#1?.md"}
