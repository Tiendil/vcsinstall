import pydantic
import pytest
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import project_path_parts

from vcsinstall.domain import (
    ContentHash,
    Dependency,
    DependencyName,
    SourcePath,
)

_EMPTY_HASH = ContentHash("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


class _DomainFields(BaseEntity):
    name: DependencyName
    source_path: SourcePath
    dependencies: dict[DependencyName, str] = pydantic.Field(default_factory=dict)


class TestDependencyName:
    @pytest.mark.parametrize("value", ["a", "ci", "ci_2", "shared-tools", "a-_0", "z9"])
    def test_accepts_project_names_in_fields(self, value: str) -> None:
        fields = _DomainFields.model_validate({"name": value, "source_path": "@/"})

        assert fields.name == value

    @pytest.mark.parametrize("value", ["", "Ci", "1ci", "-ci", "_ci", "ci/x", "ci.x", "café", None, 1, True])
    def test_rejects_invalid_name_fields(self, value: object) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            _DomainFields.model_validate({"name": value, "source_path": "@/"})

        assert caught.value.errors()[0]["loc"] == ("name",)

    def test_accepts_names_as_dictionary_keys(self) -> None:
        fields = _DomainFields.model_validate(
            {"name": "ci", "source_path": "@/", "dependencies": {"shared-tools": "repo", "ci_2": "other"}}
        )

        assert fields.dependencies == {"shared-tools": "repo", "ci_2": "other"}

    @pytest.mark.parametrize("value", ["", "Ci", "ci/x", 1])
    def test_rejects_invalid_dictionary_keys(self, value: object) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            _DomainFields.model_validate({"name": "ci", "source_path": "@/", "dependencies": {value: "repo"}})

        location = caught.value.errors()[0]["loc"]
        assert location[0] == "dependencies"
        assert location[-1] == "[key]"


class TestSourcePath:
    @pytest.mark.parametrize(
        "value",
        [
            "@/",
            "@/LICENSE",
            "@/guides/with spaces.md",
            "@/~/literal",
            "@/@/literal",
            "@/*.md",
            "@/a+b%20c",
            "@/Заметки/é.md",
            "@/notes/e\u0301.md",
            "@/notes/line\nbreak.txt",
            "@/notes/back\\slash.txt",
            "@/ docs/file",
            "@/a\0b",
        ],
    )
    def test_preserves_literal_segments_in_canonical_fields(self, value: str) -> None:
        fields = _DomainFields.model_validate({"name": "ci", "source_path": value})

        assert fields.source_path == value

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("@/.", "@/"),
            ("@/guides/..", "@/"),
            ("@/guides/a/../b.md", "@/guides/b.md"),
            ("@/guides/./notes.md", "@/guides/notes.md"),
            ("@/guides/a/../../LICENSE", "@/LICENSE"),
            ("@/guides/.././LICENSE", "@/LICENSE"),
        ],
    )
    def test_retains_normalized_source_identifiers(self, value: str, expected: str) -> None:
        fields = _DomainFields.model_validate({"name": "ci", "source_path": value})

        assert fields.source_path == expected
        assert fields.model_dump()["source_path"] == expected
        assert _DomainFields.model_validate(fields.model_dump()).source_path == expected

    def test_exposes_canonical_components_to_shared_path_operations(self) -> None:
        fields = _DomainFields.model_validate({"name": "ci", "source_path": "@/guides/a/../notes.md"})

        assert project_path_parts(fields.source_path) == ("guides", "notes.md")

    @pytest.mark.parametrize(
        "value",
        [
            "",
            ".",
            "guides/..",
            "LICENSE",
            "~/literal",
            "*.md",
            "@file",
            "@/./",
            "@/guides//notes.md",
            "@/guides/",
            "@/guides/../",
            "@/guides//../",
            "/etc/hosts",
            "//server/file",
            "@/..",
            "@/../file",
            "@/a/../../b",
            "@/a//../../b",
            "@/a/../../a",
            "@/./../a",
            "@/a/../..",
            None,
            1,
            True,
            b"LICENSE",
        ],
    )
    def test_rejects_invalid_source_fields(self, value: object) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            _DomainFields.model_validate({"name": "ci", "source_path": value})

        failure = caught.value.errors()[0]
        assert failure["loc"] == ("source_path",)
        assert failure["input"] == value

    def test_preserves_literal_text_through_serialization(self) -> None:
        fields = _DomainFields.model_validate({"name": "ci", "source_path": "@/ docs/./é+%20.md"})

        restored = _DomainFields.model_validate_json(fields.model_dump_json())

        assert restored.source_path == "@/ docs/é+%20.md"


class TestDependency:
    @pytest.mark.parametrize(
        "locator",
        [
            "https://example.com/team/shared.git?key=a+b%20c",
            "ssh://git@example.com/team/shared.git",
            "git@example.com:team/shared.git",
            "/shared/Notes and guides/",
            "literal-fixture-ID",
        ],
    )
    def test_preserves_literal_repository_text(self, locator: str) -> None:
        dependency = Dependency.model_validate({"vcs": "git", "repository": locator})

        assert dependency.repository == locator
        assert Dependency.model_validate_json(dependency.model_dump_json()).repository == locator

    @pytest.mark.parametrize(
        "fields",
        [
            {"vcs": ""},
            {"repository": ""},
            {"repository": 1},
            {"selector": {"kind": "branch", "value": ""}},
            {"selector": {"kind": "tag", "value": ""}},
            {"selector": {"kind": "revision", "value": ""}},
            {"selector": {"kind": "branch", "value": " \t\n"}},
            {"selector": {"kind": "tag", "value": " \t\n"}},
            {"selector": {"kind": "revision", "value": " \t\n"}},
            {"selector": {}},
            {"selector": {"kind": "unknown", "value": "main"}},
            {"selector": {"kind": "branch", "value": "main", "extra": True}},
            {"branch": "main"},
        ],
    )
    def test_rejects_invalid_declaration_shape(self, fields: dict[str, object]) -> None:
        with pytest.raises(pydantic.ValidationError):
            Dependency.model_validate({"vcs": "git", "repository": "source", **fields})

    def test_normalizes_selector_without_backend_policy(self) -> None:
        dependency = Dependency.model_validate(
            {"vcs": "git", "repository": "source", "selector": {"kind": "tag", "value": " \tliteral fixture\n"}}
        )

        assert dependency.selector is not None
        assert dependency.selector.value == "literal fixture"
        assert Dependency.model_validate_json(dependency.model_dump_json()).selector == dependency.selector

    @pytest.mark.parametrize(
        "kind,value", [("branch", "Release/One"), ("tag", "v1.0+fix%2Fnotes"), ("revision", "a" * 40)]
    )
    def test_nested_selector_normalization_roundtrips(self, kind: str, value: str) -> None:
        data = {"vcs": "git", "repository": "../source", "selector": {"kind": kind, "value": value}}
        dependency = Dependency.model_validate({**data, "selector": {"kind": kind, "value": f" \t{value}\n"}})

        assert dependency.model_dump(mode="json") == data
        assert Dependency.model_validate_json(dependency.model_dump_json()).selector == dependency.selector

    @pytest.mark.parametrize("backend", ["unknown", "http"])
    def test_rejects_unknown_vcs_ids(self, backend: str) -> None:
        with pytest.raises(pydantic.ValidationError):
            Dependency.model_validate({"vcs": backend, "repository": "source"})

    def test_omitted_selector_remains_omitted(self) -> None:
        dependency = Dependency.model_validate({"vcs": "filesystem", "repository": "../source"})

        assert dependency.selector is None
        assert dependency.model_dump(exclude_none=True) == {"vcs": "filesystem", "repository": "../source"}


class TestContentHash:
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "sha256:",
            "sha256:" + "a" * 63,
            "sha256:" + "a" * 65,
            "sha256:" + "A" * 64,
            "sha256:" + "g" * 64,
            "SHA256:" + "a" * 64,
            "sha512:" + "a" * 64,
            "a" * 64,
            " " + _EMPTY_HASH,
            _EMPTY_HASH + " ",
            _EMPTY_HASH + "\n",
            None,
            1,
        ],
    )
    def test_rejects_invalid_hash_values(self, value: object) -> None:
        with pytest.raises(pydantic.ValidationError):
            pydantic.TypeAdapter(ContentHash).validate_python(value)
