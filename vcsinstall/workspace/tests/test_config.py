import pydantic
import pytest

from vcsinstall.workspace import Config, Lockfile


class TestConfig:
    def test_minimal_config_defaults_and_serialization(self) -> None:
        config = Config.model_validate({"version": 1})

        assert not config.dependencies
        assert not config.entries
        assert Config.model_validate_json(config.model_dump_json()) == config

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
            {"dependencies": {"ci": {"vcs": "git", "repository": "source", "branch": ""}}},
            {"dependencies": {"ci": {"vcs": "git", "repository": "source", "branch": "main", "tag": "v1"}}},
            {"entries": [{"dependency": "missing", "source_path": "@/", "destination": "@/docs"}]},
        ],
    )
    def test_rejects_malformed_declarations(self, data: dict[str, object]) -> None:
        with pytest.raises(pydantic.ValidationError):
            Config.model_validate({"version": 1, **data})

    def test_preserves_independent_dependencies_and_literal_fields(self) -> None:
        dependency = {"vcs": "git", "repository": "../source%20+copy ", "tag": "Release+%2F"}
        config = Config.model_validate(
            {
                "version": 1,
                "dependencies": {"ci": dependency, "formatting": dependency},
                "entries": [{"dependency": "ci", "source_path": "@/notes%20+draft#1?.md ", "destination": " docs "}],
            }
        )

        assert len(config.dependencies) == 2
        assert all(item.repository == "../source%20+copy" for item in config.dependencies.values())
        assert all(item.tag == "Release+%2F" for item in config.dependencies.values())
        assert config.entries[0].source_path == "@/notes%20+draft#1?.md"
        assert config.entries[0].destination == "docs"
