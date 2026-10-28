import pydantic
import pytest
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import project_path_parts

from vcsinstall.domain import (
    BackendName,
    DependencyName,
    RepositoryIdentity,
    RepositoryLocator,
    SelectorKind,
    SelectorValue,
    SourcePath,
    VersionSelector,
)


class _DomainFields(BaseEntity):
    name: DependencyName
    source_path: SourcePath
    dependencies: dict[DependencyName, str] = pydantic.Field(default_factory=dict)


class TestDependencyName:
    @pytest.mark.parametrize("value", ["a", "ci", "ci_2", "shared-tools", "a-_0", "z9"])
    def test_accepts_project_names_in_fields(self, value: str) -> None:
        fields = _DomainFields.model_validate({"name": value, "source_path": "@/"})

        assert fields.name == value

    @pytest.mark.parametrize(
        "value", ["", " ci", "ci ", "Ci", "1ci", "-ci", "_ci", "ci/x", "ci.x", "café", "ci\n", None, 1, True, b"ci"]
    )
    def test_rejects_invalid_name_fields(self, value: object) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            _DomainFields.model_validate({"name": value, "source_path": "@/"})

        assert caught.value.errors()[0]["loc"] == ("name",)

    def test_accepts_names_as_dictionary_keys(self) -> None:
        fields = _DomainFields.model_validate(
            {"name": "ci", "source_path": "@/", "dependencies": {"shared-tools": "repo", "ci_2": "other"}}
        )

        assert fields.dependencies == {"shared-tools": "repo", "ci_2": "other"}

    @pytest.mark.parametrize("value", ["", " ci", "Ci", "ci/x", "ci\n", 1, b"ci"])
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
            "@/ docs/file ",
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
            " @/docs",
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
        fields = _DomainFields.model_validate({"name": "ci", "source_path": "@/ docs/./é+%20.md "})

        restored = _DomainFields.model_validate_json(fields.model_dump_json())

        assert restored.source_path == "@/ docs/é+%20.md "


class TestRepositoryIdentity:
    @pytest.mark.parametrize(
        "locator",
        [
            "https://example.com/team/shared.git?key=a+b%20c",
            "ssh://git@example.com/team/shared.git",
            "git@example.com:team/shared.git",
            "/shared/Notes and guides/ ",
            "literal-fixture-ID",
        ],
    )
    def test_preserves_native_locator(self, locator: str) -> None:
        identity = RepositoryIdentity(backend=BackendName("git"), locator=RepositoryLocator(locator))
        assert identity.locator == locator

    @pytest.mark.parametrize("backend,locator", [("", "repo"), ("git", ""), ("", "")])
    def test_rejects_empty_components(self, backend: str, locator: str) -> None:
        with pytest.raises(pydantic.ValidationError):
            RepositoryIdentity(backend=BackendName(backend), locator=RepositoryLocator(locator))

    def test_serialization_preserves_identity(self) -> None:
        identity = RepositoryIdentity(
            backend=BackendName("filesystem"), locator=RepositoryLocator("/source/Заметки/ ")
        )
        assert RepositoryIdentity.model_validate_json(identity.model_dump_json()) == identity


class TestVersionSelector:
    @pytest.mark.parametrize("value", ["Release/One", "v1.0+fix%2Fnotes", " literal ", "opaque-revision"])
    def test_preserves_literal_value(self, value: str) -> None:
        selector = VersionSelector(kind=SelectorKind.tag, value=SelectorValue(value))
        assert selector.value == value

    def test_rejects_empty_value(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            VersionSelector(kind=SelectorKind.branch, value=SelectorValue(""))

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            VersionSelector.model_validate({"kind": "latest", "value": "main"})

    def test_serialization_preserves_selection(self) -> None:
        selector = VersionSelector(kind=SelectorKind.revision, value=SelectorValue("opaque:revision+ID"))
        assert VersionSelector.model_validate_json(selector.model_dump_json()) == selector
