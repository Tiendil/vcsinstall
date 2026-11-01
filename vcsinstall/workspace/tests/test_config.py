import pydantic
import pytest
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import DependencyName
from vcsinstall.workspace import Config, Lockfile


class TestConfig:
    def test_minimal_config_defaults_and_serialization(self) -> None:
        config = Config.model_validate({"version": 1})

        assert not config.dependencies
        assert not config.entries
        assert not config.entries_for_dependency(DependencyName("ci"))
        assert config.entry_for_destination(ProjectPathId("@/missing")) is None
        assert Config.model_validate_json(config.model_dump_json()) == config

    @pytest.mark.parametrize("name,indices", [("ci", (0, 2)), ("formatting", (1,)), ("unused", ()), ("missing", ())])
    def test_entries_for_dependency__selects_in_stored_order(self, name: str, indices: tuple[int, ...]) -> None:
        definition = {"vcs": "noop", "repository": "fixture"}
        config = Config.model_validate(
            {
                "version": 1,
                "dependencies": {name: definition for name in ("ci", "formatting", "unused")},
                "entries": [
                    {"dependency": "ci", "source_path": "@/", "destination": "@/z"},
                    {"dependency": "formatting", "source_path": "@/", "destination": "@/middle"},
                    {"dependency": "ci", "source_path": "@/", "destination": "@/a"},
                ],
            }
        )

        assert config.entries_for_dependency(DependencyName(name)) == tuple(config.entries[index] for index in indices)

    @pytest.mark.parametrize(
        "destination,index",
        [
            ("@/first", 0),
            ("@/second", 1),
            ("@/first/child", None),
            ("@/missing", None),
        ],
    )
    def test_entry_for_destination__selects_only_an_exact_destination(
        self, destination: str, index: int | None
    ) -> None:
        config = Config.model_validate(
            {
                "version": 1,
                "dependencies": {"ci": {"vcs": "noop", "repository": "fixture"}},
                "entries": [
                    {"dependency": "ci", "source_path": "@/", "destination": "@/first"},
                    {"dependency": "ci", "source_path": "@/", "destination": "@/second"},
                ],
            }
        )

        result = config.entry_for_destination(ProjectPathId(destination))

        assert result is (config.entries[index] if index is not None else None)

    @pytest.mark.parametrize("version", [None, True, False, 1.0, "1", 0, 2, -1])
    @pytest.mark.parametrize("model", [Config, Lockfile])
    def test_rejects_invalid_versions(self, version: object, model: type[Config] | type[Lockfile]) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            model.model_validate({"version": version})

        assert caught.value.errors()[0]["loc"] == ("version",)
        assert repr(version) in str(caught.value)

    @pytest.mark.parametrize("model", [Config, Lockfile])
    def test_requires_version(self, model: type[Config] | type[Lockfile]) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            model.model_validate({})

        assert caught.value.errors()[0]["loc"] == ("version",)

    @pytest.mark.parametrize(
        "data",
        [
            {"unknown": True},
            {"dependencies": []},
            {"entries": {}},
            {"dependencies": {"Bad": {"vcs": "git", "repository": "source"}}},
            {"dependencies": {"ci": {"vcs": "git", "repository": "source", "extra": True}}},
            {"dependencies": {"ci": {"repository": "source"}}},
            {"dependencies": {"ci": {"vcs": "git", "repository": ""}}},
            {
                "dependencies": {
                    "ci": {"vcs": "git", "repository": "source", "selector": {"kind": "branch", "value": ""}}
                }
            },
            {
                "dependencies": {
                    "ci": {"vcs": "git", "repository": "source", "selector": {"kind": "unknown", "value": "main"}}
                }
            },
            {"entries": [{"dependency": "missing", "source_path": "@/", "destination": "@/docs"}]},
        ],
    )
    def test_rejects_malformed_declarations(self, data: dict[str, object]) -> None:
        with pytest.raises(pydantic.ValidationError):
            Config.model_validate({"version": 1, **data})

    @pytest.mark.parametrize("model", [Config, Lockfile])
    @pytest.mark.parametrize(
        "destination",
        [
            "",
            "docs",
            "./docs",
            "../docs",
            "/project/docs",
            "@docs",
            "@/a/../docs",
            "@/./docs",
            "@/a//docs",
            "@/../docs",
        ],
    )
    def test_rejects_noncanonical_destinations(self, model: type[Config] | type[Lockfile], destination: str) -> None:
        definition = {"vcs": "noop", "repository": "../source"}
        mapping = {"dependency": "ci", "source_path": "@/guide", "destination": destination}
        if model is Lockfile:
            definition["revision"] = "recorded-revision"
            mapping["kind"] = "absent"

        with pytest.raises(pydantic.ValidationError) as caught:
            model.model_validate({"version": 1, "dependencies": {"ci": definition}, "entries": [mapping]})

        assert caught.value.errors()[0]["loc"][-1] == "destination"

    def test_preserves_independent_dependencies_and_literal_fields(self) -> None:
        dependency = {
            "vcs": "git",
            "repository": "../source%20+copy ",
            "selector": {"kind": "tag", "value": "Release+%2F"},
        }
        config = Config.model_validate(
            {
                "version": 1,
                "dependencies": {"ci": dependency, "formatting": dependency},
                "entries": [{"dependency": "ci", "source_path": "@/notes%20+draft#1?.md ", "destination": " @/docs "}],
            }
        )

        assert len(config.dependencies) == 2
        assert all(item.repository == "../source%20+copy" for item in config.dependencies.values())
        assert all(
            item.selector is not None and item.selector.value == "Release+%2F" for item in config.dependencies.values()
        )
        assert config.entries[0].source_path == "@/notes%20+draft#1?.md"
        assert config.entries[0].destination == "@/docs"
