import json
import os
import tomllib
from pathlib import Path

import pytest
import tomli_w
from llm_tool_cli.config import errors as config_errors
from llm_tool_cli.config.project import project_config
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err, Result
from llm_tool_cli.paths import ProjectPathId
from llm_tool_cli.paths import errors as path_errors
from llm_tool_cli.protocol.tests.helpers import cell_payloads
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cache import active_cache_root
from vcsinstall.cli.application import app
from vcsinstall.cli.commands import entries
from vcsinstall.cli.commands.tests.helpers import (
    declare_entry,
    invoke_add,
    invoke_entry_add,
    invoke_entry_list,
    invoke_entry_remove,
    invoke_install,
)
from vcsinstall.filesystem import FSClient
from vcsinstall.filesystem import errors as filesystem_errors
from vcsinstall.filesystem import hashes
from vcsinstall.runtime import errors as runtime_errors
from vcsinstall.sources import errors as source_errors
from vcsinstall.vcs_clients import GitClient
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import active_workspace
from vcsinstall.workspace import errors as workspace_errors


class TestListEntries:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "custom.toml"
        definition = {
            "vcs": "git",
            "repository": "https://example.test/shared.git",
            "selector": {"kind": "branch", "value": "main"},
        }
        config.write_text(tomli_w.dumps({"version": 1, "dependencies": dict.fromkeys(("ci", "unused"), definition)}))
        config.with_name("vcsinstall.lock").write_text(
            tomli_w.dumps(
                {
                    "version": 1,
                    "dependencies": {name: {**definition, "revision": "1" * 40} for name in ("ci", "unused")},
                }
            )
        )
        return config

    def test_reports_ordered_installations_without_source_cache_or_project_writes(  # noqa: CCR001 — Mixed states.
        self, config: Path, mocker: MockerFixture
    ) -> None:
        states = (
            ("unmodified", b"original", b"original", "unmodified"),
            ("modified", b"original", b"edited", "modified"),
            ("missing", b"original", None, "missing"),
            ("directory", {"a": b"a", "nested/b": b"b"}, {"a": b"a", "nested/b": b"b"}, "unmodified"),
            ("partial", {"a": b"a", "b": b"b"}, {"a": b"a"}, "missing"),
            ("mixed", {"a": b"a", "b": b"b"}, {"a": b"edited"}, "modified"),
            ("addition", {"a": b"a"}, {"a": b"a", ".extra": b"new"}, "modified"),
        )
        for destination, expected, actual, _ in states:
            declare_entry(config, source="@/" + destination, destination="@/" + destination, files=expected)
            local = (
                {destination: actual}
                if isinstance(actual, bytes)
                else {destination + "/" + child: data for child, data in (actual or {}).items()}
            )
            for path, data in local.items():
                target = config.parent / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        declarations = tomllib.loads(config.read_text())
        declarations["entries"].append({"dependency": "ci", "source_path": "@/new", "destination": "@/unlocked"})
        config.write_text(tomli_w.dumps(declarations))
        before = {path: path.read_bytes() for path in config.parent.rglob("*") if path.is_file()}
        for name in (
            "vcsinstall.cli.preparation.resolve_cache_root",
            "vcsinstall.runtime.commands.get_hosting",
            "vcsinstall.cache.paths.user_cache_path",
            "vcsinstall.vcs_clients.GitClient.run",
            "vcsinstall.sources.adapters.git.GitSourceAdapter.resolve",
            "vcsinstall.sources.adapters.git.GitSourceAdapter.inspect",
        ):
            mocker.patch(name, side_effect=AssertionError("Unexpected source or cache access"))

        statuses = {destination: status for destination, _, _, status in states} | {"unlocked": "unlocked"}
        expected_records = [
            {
                "content": None,
                "type": "installation",
                "operation": "list",
                "dependency": "ci",
                "destination": "@/" + destination,
                "vcs": "git",
                "repository": "https://example.test/shared.git",
                "source_path": "@/" + destination if destination != "unlocked" else "@/new",
                "ref_kind": "branch",
                "ref": "main",
                "revision": "1" * 40 if status != "unlocked" else None,
                "status": status,
            }
            for destination, status in sorted(statuses.items())
        ]
        for _ in range(2):
            result = CliRunner().invoke(
                app, ["-p", "automation", "--config", str(config), "--cache-dir", str(config / "bad"), "list"]
            )

            assert result.exit_code == 0, result.output
            assert not result.stderr
            assert cell_payloads([json.loads(line) for line in result.stdout.splitlines()]) == expected_records
            assert not project_config.is_set() and not active_workspace.is_set() and not active_cache_root.is_set()
        assert {path: path.read_bytes() for path in config.parent.rglob("*") if path.is_file()} == before

    def test_normalizes_selection_from_invocation_directory_and_inspects_each_entry_once(
        self, config: Path, mocker: MockerFixture
    ) -> None:
        for destination in ("@/installed.txt", "@/docs/shared", "@/unselected"):
            declare_entry(config, source="@/source", destination=destination, files=b"base")
        (config.parent / "unselected").symlink_to(config.parent.parent, target_is_directory=True)
        nested = config.parent / "nested"
        nested.mkdir()
        mocker.patch("pathlib.Path.cwd", return_value=nested)
        inspect = mocker.spy(FSClient, "inspect_tree")

        code, records, output = invoke_entry_list(
            config,
            "../installed.txt",
            str(config.parent / "docs/shared"),
            " @/docs/unused/../shared ",
            "@/installed.txt",
        )

        assert code == 0, output
        assert [record["destination"] for record in records] == ["@/docs/shared", "@/installed.txt"]
        assert all(record["status"] == "missing" and record["revision"] == "1" * 40 for record in records)
        assert [call.args[1] for call in inspect.call_args_list] == ["@/docs/shared", "@/installed.txt"]
        assert (config.parent / "unselected").is_symlink()

    def test_selection_spans_dependencies_and_reports_effective_source_identity(
        self, config: Path, mocker: MockerFixture
    ) -> None:
        for destination in ("@/z-git", "@/a-files"):
            declare_entry(config, source="@/source", destination=destination, files=b"base")
        definition = {"vcs": "filesystem", "repository": "../unavailable-files"}
        for path in (config, config.with_name("vcsinstall.lock")):
            document = tomllib.loads(path.read_text())
            document["dependencies"]["local"] = (
                definition if path == config else {**definition, "revision": hashes.hash_file(b"base")}
            )
            document["entries"][1]["dependency"] = "local"
            path.write_text(tomli_w.dumps(document))
        mocker.patch("pathlib.Path.cwd", return_value=config.parent.parent)

        code, records, output = invoke_entry_list(config, "@/z-git", "@/a-files")

        assert code == 0, output
        assert [record["dependency"] for record in records] == ["local", "ci"]
        assert records[0]["repository"] == str(config.parent.parent / "unavailable-files")
        assert (records[0]["ref_kind"], records[0]["ref"], records[0]["revision"]) == (
            "default",
            None,
            hashes.hash_file(b"base"),
        )
        assert not (config.parent.parent / "unavailable-files").exists()

    @pytest.mark.parametrize("unknown", ["@/unknown", "@/docs", "@/docs/shared/guide", "@/docs/*"])
    def test_unknown_selection_fails_before_inspection(
        self, config: Path, unknown: str, mocker: MockerFixture
    ) -> None:
        declare_entry(config, source="@/source", destination="@/docs/shared", files={"guide": b"base"})
        inspect = mocker.patch(
            "vcsinstall.filesystem.FSClient.inspect_tree", side_effect=AssertionError("Unexpected inspection")
        )

        code, records, output = invoke_entry_list(config, "@/docs/shared", unknown)

        assert code == 1, output
        assert [record["type"] for record in records] == ["error"]
        assert unknown in str(records[0]["content"])
        inspect.assert_not_called()

    @pytest.mark.parametrize("path", ["", "   ", "@/../outside", "../outside"])
    def test_invalid_paths_preserve_shared_diagnostics(self, config: Path, path: str, mocker: MockerFixture) -> None:
        mocker.patch("pathlib.Path.cwd", return_value=config.parent)

        code, records, output = invoke_entry_list(config, path)

        assert code == 3, output
        assert [record["type"] for record in records] == ["error"]
        assert records[0]["code"] == path_errors.InvalidProjectPath.model_fields["code"].default
        assert "path" in records[0]

    def test_argument_separator_accepts_a_leading_hyphen(self, config: Path, mocker: MockerFixture) -> None:
        declare_entry(config, source="@/source", destination="@/-entry", files=b"base")
        mocker.patch("pathlib.Path.cwd", return_value=config.parent)

        code, records, output = invoke_entry_list(config, "--", "-entry")

        assert code == 0, output
        assert records[0]["destination"] == "@/-entry"

    @pytest.mark.parametrize(
        "state",
        [
            "absent_lockfile",
            "missing_lock",
            "repository",
            "selector",
            "baseline",
            "source_path",
            "dependency",
            "destination",
        ],
    )
    def test_unusable_lock_or_baseline_reports_null_revision(  # noqa: CCR001 — Missing and stale record variants.
        self, config: Path, state: str
    ) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=b"base")
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        if state == "absent_lockfile":
            lockfile.unlink()
        else:
            if state == "missing_lock":
                recorded["dependencies"].pop("ci")
                recorded["entries"] = []
            elif state == "repository":
                recorded["dependencies"]["ci"]["repository"] = "https://example.test/other.git"
            elif state == "selector":
                recorded["dependencies"]["ci"]["selector"] = {"kind": "tag", "value": "main"}
            elif state == "baseline":
                recorded["entries"] = []
            else:
                recorded["entries"][0][state] = "unused" if state == "dependency" else "@/other"
            lockfile.write_text(tomli_w.dumps(recorded))
        before = (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None)

        code, records, output = invoke_entry_list(config)

        assert code == 0, output
        assert records[0]["status"] == "unlocked"
        assert records[0]["revision"] is None
        assert (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None) == before

    @pytest.mark.parametrize("lock_kind", ["branch", "tag", "revision", None])
    def test_omitted_git_selector_uses_only_a_matching_branch_lock(  # noqa: CCR001 — Retained-selector variants.
        self, config: Path, lock_kind: str | None
    ) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=b"base")
        document = tomllib.loads(config.read_text())
        document["dependencies"]["ci"].pop("selector")
        config.write_text(tomli_w.dumps(document))
        lockfile = config.with_name("vcsinstall.lock")
        if lock_kind is None:
            lockfile.unlink()
        else:
            recorded = tomllib.loads(lockfile.read_text())
            recorded["dependencies"]["ci"]["selector"] = {
                "kind": lock_kind,
                "value": "1" * 40 if lock_kind == "revision" else "main",
            }
            lockfile.write_text(tomli_w.dumps(recorded))

        code, records, output = invoke_entry_list(config)

        assert code == 0, output
        matching = lock_kind == "branch"
        assert (records[0]["ref_kind"], records[0]["ref"]) == (
            "branch" if matching else "default",
            "main" if matching else None,
        )
        assert records[0]["revision"] == ("1" * 40 if matching else None)
        assert records[0]["status"] == ("missing" if matching else "unlocked")

    @pytest.mark.parametrize("kind", ["directory", "absent"])
    @pytest.mark.parametrize(
        "local,status", [("absent", "unmodified"), ("directory", "unmodified"), ("file", "modified")]
    )
    def test_empty_baselines_and_expected_absence(self, config: Path, kind: str, local: str, status: str) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files={})
        if kind == "absent":
            lockfile = config.with_name("vcsinstall.lock")
            recorded = tomllib.loads(lockfile.read_text())
            recorded["entries"][0] = {
                key: value for key, value in recorded["entries"][0].items() if key not in ("hash", "files")
            } | {"kind": "absent"}
            lockfile.write_text(tomli_w.dumps(recorded))
        if local == "directory":
            (config.parent / "installed/empty").mkdir(parents=True)
        elif local == "file":
            (config.parent / "installed").write_bytes(b"")

        code, records, output = invoke_entry_list(config)

        assert code == 0, output
        assert records[0]["status"] == status
        assert records[0]["revision"] == "1" * 40

    def test_regular_file_obstructing_a_parent_is_modified(self, config: Path) -> None:
        declare_entry(config, source="@/source", destination="@/parent/installed", files=b"base")
        (config.parent / "parent").write_bytes(b"obstruction")

        code, records, output = invoke_entry_list(config)

        assert code == 0, output
        assert records[0]["status"] == "modified"

    @pytest.mark.parametrize("locked", [False, True])
    def test_unreadable_content_fails_without_partial_results(
        self, config: Path, locked: bool, mocker: MockerFixture
    ) -> None:
        declare_entry(config, source="@/source", destination="@/a-missing", files=b"base")
        declare_entry(config, source="@/source", destination="@/z-unreadable", files=b"base")
        (config.parent / "z-unreadable").write_bytes(b"base")
        if not locked:
            config.with_name("vcsinstall.lock").unlink()
        mocker.patch(
            "vcsinstall.filesystem.client.hashlib.file_digest", side_effect=PermissionError("Unreadable file")
        )

        code, records, output = invoke_entry_list(config)

        assert code == 3, output
        assert [record["type"] for record in records] == ["error"]
        assert records[0]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert records[0]["path"] == str(config.parent / "z-unreadable")

    @pytest.mark.parametrize("child", [False, True])
    def test_symlinks_in_selected_content_fail(self, config: Path, child: bool) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files={"a": b"base"})
        link = config.parent / "installed"
        if child:
            link.mkdir()
            link = link / "a"
        link.symlink_to(config)

        code, records, output = invoke_entry_list(config)

        assert code == 3, output
        assert [record["type"] for record in records] == ["error"]
        assert records[0]["code"] == path_errors.InvalidProjectPath.model_fields["code"].default

    @pytest.mark.parametrize("filename", ["custom.toml", "vcsinstall.lock"])
    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_invalid_project_files_use_shared_errors(self, config: Path, filename: str, protocol: str) -> None:
        path = config.parent / filename
        path.write_text("version = [")

        result = CliRunner().invoke(app, ["-p", protocol, "--config", str(config), "list"])

        assert result.exit_code == 2, result.output
        assert str(path) in result.stdout
        assert not result.stderr
        assert not project_config.is_set() and not active_workspace.is_set()
        if protocol == "automation":
            assert json.loads(result.stdout)["type"] == "error"

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_each_entry_identifies_its_dependency_in_every_protocol(self, config: Path, protocol: str) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=b"base")

        result = CliRunner().invoke(app, ["-p", protocol, "--config", str(config), "list"])

        assert result.exit_code == 0, result.output
        assert not result.stderr
        if protocol == "automation":
            assert json.loads(result.stdout)["dependency"] == "ci"
        elif protocol == "llm":
            assert "--VCSINSTALL-CELL " in result.stdout
            assert "dependency=ci\n" in result.stdout
        else:
            assert "dependency: ci\n" in result.stdout

    @pytest.mark.parametrize("with_dependencies", [False, True])
    def test_empty_entry_selection_succeeds_without_results_or_lockfile(
        self, config: Path, with_dependencies: bool
    ) -> None:
        if not with_dependencies:
            config.write_text("version = 1\n")
        config.with_name("vcsinstall.lock").unlink()

        code, records, output = invoke_entry_list(config)

        assert code == 0, output
        assert not records and not output
        assert not config.with_name("vcsinstall.lock").exists()

    def test_missing_explicit_config_does_not_fall_back(self, config: Path, mocker: MockerFixture) -> None:
        mocker.patch("pathlib.Path.cwd", return_value=config.parent)
        missing = config.with_name("missing.toml")

        code, records, output = invoke_entry_list(missing)

        assert code == 2, output
        assert records[0]["path"] == str(missing)
        assert not missing.exists()

    def test_discovers_the_nearest_project_from_a_subdirectory(self, config: Path, mocker: MockerFixture) -> None:
        declare_entry(config, source="@/source", destination="@/installed", files=b"base")
        (config.parent / "vcsinstall.toml").write_bytes(config.read_bytes())
        (config.parent.parent / "vcsinstall.toml").write_text("invalid[")
        nested = config.parent / "nested"
        nested.mkdir()
        mocker.patch("pathlib.Path.cwd", return_value=nested)

        result = CliRunner().invoke(app, ["-p", "automation", "list", "../installed"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["destination"] == "@/installed"

    @pytest.mark.parametrize("option", ["--force", "--locked", "--protocol", "--config"])
    def test_command_options_fail_before_project_loading(self, option: str, mocker: MockerFixture) -> None:
        load = mocker.patch.object(entries, "open_workspace", side_effect=AssertionError("Unexpected loading"))

        result = CliRunner().invoke(app, ["list", option])

        assert result.exit_code == 2
        load.assert_not_called()


@pytest.mark.usefixtures("isolated_settings")
class TestRemoveEntries:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "custom.toml"
        definition = (
            'version = 1\n[dependencies.ci]\nvcs = "git"\nrepository = "../unavailable-source"\n'
            'selector = { kind = "branch", value = "main" }\n'
        )
        config.write_text(definition)
        config.with_name("vcsinstall.lock").write_text(definition + 'revision = "' + "1" * 40 + '"\n')
        declare_entry(config, source="@/guide", destination="@/installed.txt", files=b"original")
        declare_entry(
            config,
            source="@/guides",
            destination="@/docs/shared",
            files={"guide": b"guide", "nested/second": b"second"},
        )
        declare_entry(config, source="@/other", destination="@/unselected", files=b"unselected")
        (project / "installed.txt").write_bytes(b"original")
        (project / "docs/shared/nested").mkdir(parents=True)
        (project / "docs/shared/guide").write_bytes(b"guide")
        (project / "docs/shared/nested/second").write_bytes(b"second")
        (project / "unselected").write_bytes(b"unselected")
        return config

    def test_removes_complete_selection_once_and_preserves_shared_locks(self, config: Path) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        declarations = tomllib.loads(config.read_text())
        recorded = tomllib.loads(lockfile.read_text())
        (config.parent / "docs/shared/empty").mkdir()
        (config.parent / "unselected").write_bytes(b"unselected local edits")

        code, records, output = invoke_entry_remove(
            config, "@/installed.txt", "@/docs/shared", " @/docs/unused/../shared "
        )

        assert code == 0, output
        assert [record["destination"] for record in records] == ["@/docs/shared", "@/installed.txt"]
        for record in records:
            assert record["type"] == "removal"
            assert record["operation"] == "remove"
            assert record["dependency"] == "ci"
            assert record["outcome"] == "removed"
            assert record["keep_files"] is False
            assert record["content"] is None
            assert "status" not in record and "revision" not in record
        assert not (config.parent / "installed.txt").exists()
        assert not (config.parent / "docs/shared").exists()
        assert (config.parent / "docs").is_dir()
        assert (config.parent / "unselected").read_bytes() == b"unselected local edits"
        updated = tomllib.loads(config.read_text())
        locked = tomllib.loads(lockfile.read_text())
        assert updated["dependencies"] == declarations["dependencies"]
        assert locked["dependencies"] == recorded["dependencies"]
        assert updated["entries"] == [declarations["entries"][2]]
        assert locked["entries"] == [recorded["entries"][2]]
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()

    def test_selection_spans_dependencies_and_preserves_locks_after_last_entry(self, config: Path) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        declared = tomllib.loads(config.read_text())
        locked = tomllib.loads(lockfile.read_text())
        definition = {"vcs": "filesystem", "repository": "../unavailable-files"}
        declared["dependencies"]["local"] = definition
        locked["dependencies"]["local"] = {**definition, "revision": "sha256:" + "2" * 64}
        declared["entries"][1]["dependency"] = "local"
        locked["entries"][1]["dependency"] = "local"
        config.write_text(tomli_w.dumps(declared))
        lockfile.write_text(tomli_w.dumps(locked))

        code, records, output = invoke_entry_remove(config, "@/unselected", "@/installed.txt", "@/docs/shared")

        assert code == 0, output
        assert cell_payloads(records) == [
            {
                "content": None,
                "type": "removal",
                "operation": "remove",
                "dependency": name,
                "destination": destination,
                "outcome": "removed",
                "keep_files": False,
            }
            for name, destination in (("local", "@/docs/shared"), ("ci", "@/installed.txt"), ("ci", "@/unselected"))
        ]
        assert tomllib.loads(config.read_text()) == {**declared, "entries": []}
        assert tomllib.loads(lockfile.read_text()) == {**locked, "entries": []}

    @pytest.mark.parametrize("kind", ["directory", "absent"])
    @pytest.mark.parametrize("existing", [False, True])
    def test_empty_and_absent_baselines_allow_structural_directory_equivalence(
        self, config: Path, kind: str, existing: bool
    ) -> None:
        declare_entry(config, source="@/empty", destination="@/empty", files={})
        lockfile = config.with_name("vcsinstall.lock")
        if kind == "absent":
            recorded = tomllib.loads(lockfile.read_text())
            recorded["entries"][-1].pop("files")
            recorded["entries"][-1].pop("hash")
            recorded["entries"][-1]["kind"] = "absent"
            lockfile.write_text(tomli_w.dumps(recorded))
        if existing:
            (config.parent / "empty/nested").mkdir(parents=True)

        code, records, output = invoke_entry_remove(config, "@/empty")

        assert code == 0, output
        assert records[0]["outcome"] == "removed"
        assert not (config.parent / "empty").exists()

    @pytest.mark.parametrize("form", ["relative", "absolute", "identifier"])
    def test_normalizes_entry_paths_from_invocation_directory(
        self, config: Path, mocker: MockerFixture, form: str
    ) -> None:
        invocation = config.parent / "docs"
        mocker.patch("pathlib.Path.cwd", return_value=invocation)
        entry_path = {
            "relative": " ./unused/../shared ",
            "absolute": str(invocation / "shared"),
            "identifier": " @/docs/unused/../shared ",
        }[form]

        code, records, output = invoke_entry_remove(config, entry_path)

        assert code == 0, output
        assert len(records) == 1
        assert records[0]["destination"] == "@/docs/shared"
        assert (config.parent / "installed.txt").read_bytes() == b"original"

    @pytest.mark.parametrize("arguments", [[], ["--force"], ["--keep-files"], ["--keep-files", "--force"]])
    def test_requires_an_explicit_selection_before_project_loading(self, tmp_path: Path, arguments: list[str]) -> None:
        result = CliRunner().invoke(app, ["--config", str(tmp_path / "missing"), "remove", *arguments])

        assert result.exit_code == 2, result.output
        assert not tuple(tmp_path.iterdir())
        assert not active_workspace.is_set()

    @pytest.mark.parametrize("entry_path", ["@/missing", "@/docs/shared/guide", "@/docs", "@/docs/*", "@/"])
    def test_unknown_or_nonroot_selection_preserves_every_selected_entry(self, config: Path, entry_path: str) -> None:
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_remove(config, "@/installed.txt", entry_path)

        assert code == 1, output
        assert records[0]["type"] == "error"
        assert entry_path in output
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert (config.parent / "installed.txt").read_bytes() == b"original"

    @pytest.mark.parametrize("change", ["edited", "addition", "hidden", "obstruction", "root_file"])
    @pytest.mark.parametrize("force", [False, True])
    def test_complete_tree_protection_and_forced_removal(  # noqa: CCR001 — Exercise distinct content-change cases.
        self, config: Path, change: str, force: bool
    ) -> None:
        directory = config.parent / "docs/shared"
        if change == "edited":
            (directory / "guide").write_bytes(b"edited")
        elif change in ("addition", "hidden"):
            (directory / ("generated" if change == "addition" else ".hidden")).write_bytes(b"new")
        elif change == "obstruction":
            (directory / "nested/second").unlink()
            (directory / "nested").rmdir()
            (directory / "nested").write_bytes(b"obstructing regular file")
        else:
            (directory / "nested/second").unlink()
            (directory / "nested").rmdir()
            (directory / "guide").unlink()
            directory.rmdir()
            directory.write_bytes(b"replaced directory")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_remove(
            config, *(["--force"] if force else []), "@/installed.txt", "@/docs/shared"
        )

        assert code == (0 if force else 3), output
        if force:
            assert not directory.exists()
            assert not (config.parent / "installed.txt").exists()
        else:
            assert records[0]["code"] == runtime_errors.RemovalConflict.model_fields["code"].default
            assert records[0]["destination"] == "@/docs/shared"
            assert "--keep-files" in output and "--force" in output
            assert directory.exists()
            assert (config.parent / "installed.txt").read_bytes() == b"original"
            assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    @pytest.mark.parametrize("missing", ["file", "directory", "child", "empty_at_file"])
    def test_missing_content_does_not_block_removal(self, config: Path, missing: str) -> None:
        root = config.parent
        if missing == "file":
            (root / "installed.txt").unlink()
        elif missing == "directory":
            (root / "docs/shared").rename(root / "retained-outside-selection")
        elif missing == "child":
            (root / "docs/shared/guide").unlink()
        else:
            (root / "installed.txt").unlink()
            (root / "installed.txt").mkdir()

        code, records, output = invoke_entry_remove(config, "@/installed.txt", "@/docs/shared")

        assert code == 0, output
        assert len(records) == 2
        assert not (root / "installed.txt").exists()
        assert not (root / "docs/shared").exists()
        assert (root / "unselected").read_bytes() == b"unselected"

    @pytest.mark.parametrize("force", [False, True])
    def test_keep_files_preserves_all_content_without_inspecting_it(
        self, config: Path, mocker: MockerFixture, force: bool
    ) -> None:
        from vcsinstall.filesystem import FSClient

        target = config.parent / "docs/shared"
        (target / "guide").write_bytes(b"local changes")
        (target / "extra").write_bytes(b"local addition")
        (target / "link").symlink_to(config.parent / "unselected")
        os.utime(target / "guide", ns=(1, 1))
        config.with_name("vcsinstall.lock").unlink()
        inspect = mocker.patch.object(FSClient, "inspect_tree", side_effect=AssertionError("unexpected inspection"))

        code, records, output = invoke_entry_remove(
            config, "--keep-files", *(["--force"] if force else []), "@/docs/shared"
        )

        assert code == 0, output
        assert records[0]["keep_files"] is True
        assert (target / "guide").read_bytes() == b"local changes"
        assert (target / "guide").stat().st_mtime_ns == 1
        assert (target / "extra").read_bytes() == b"local addition"
        assert (target / "nested/second").read_bytes() == b"second"
        assert (target / "link").is_symlink()
        assert not config.with_name("vcsinstall.lock").exists()
        inspect.assert_not_called()

    @pytest.mark.parametrize("state", ["missing_file", "empty_lock", "missing_baseline", "source", "dependency"])
    @pytest.mark.parametrize("mode", ["normal", "force", "keep", "absent"])
    def test_unavailable_or_mismatching_baselines_require_an_applicable_bypass(  # noqa: CCR001 — State combinations.
        self, config: Path, state: str, mode: str
    ) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        if state == "missing_file":
            lockfile.unlink()
        elif state == "empty_lock":
            lockfile.write_text("# retained empty state\nversion = 1\n")
        elif state == "missing_baseline":
            recorded["entries"] = recorded["entries"][1:]
            lockfile.write_text(tomli_w.dumps(recorded))
        else:
            declared = tomllib.loads(config.read_text())
            if state == "source":
                declared["entries"][0]["source_path"] = "@/new-source"
            else:
                declared["dependencies"]["other"] = declared["dependencies"]["ci"]
                declared["entries"][0]["dependency"] = "other"
                recorded["dependencies"]["other"] = recorded["dependencies"]["ci"]
                lockfile.write_text(tomli_w.dumps(recorded))
            config.write_text(tomli_w.dumps(declared))
        if mode == "absent":
            (config.parent / "installed.txt").unlink()
        before = config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None
        options = {"normal": [], "force": ["--force"], "keep": ["--keep-files"], "absent": []}[mode]

        code, records, output = invoke_entry_remove(config, *options, "@/installed.txt")

        assert code == (3 if mode == "normal" else 0), output
        assert lockfile.exists() == (state != "missing_file")
        if mode == "normal":
            assert records[0]["type"] == "error"
            assert str(lockfile) in output
            assert (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None) == before
            assert (config.parent / "installed.txt").read_bytes() == b"original"
        elif mode == "keep":
            assert records[0]["keep_files"] is True
            assert (config.parent / "installed.txt").read_bytes() == b"original"
        else:
            assert not (config.parent / "installed.txt").exists()
        if state == "empty_lock":
            assert lockfile.read_bytes() == before[1]

    @pytest.mark.parametrize("change", ["repository", "selector", "backend"])
    def test_dependency_lock_must_match_for_verification(self, config: Path, change: str) -> None:
        declared = tomllib.loads(config.read_text())
        if change == "repository":
            declared["dependencies"]["ci"]["repository"] = "other-source"
        elif change == "selector":
            declared["dependencies"]["ci"]["selector"]["value"] = "other"
        else:
            declared["dependencies"]["ci"] = {"vcs": "filesystem", "repository": "other-source"}
        config.write_text(tomli_w.dumps(declared))
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_remove(config, "@/installed.txt")

        assert code == 3, output
        assert records[0]["code"] == workspace_errors.DependencyLockRequired.model_fields["code"].default
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert (config.parent / "installed.txt").read_bytes() == b"original"

    @pytest.mark.parametrize("mode", [[], ["--force"], ["--keep-files"]])
    def test_invalid_lockfile_is_never_bypassed(self, config: Path, mode: list[str]) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        lockfile.write_text("invalid TOML [")
        before = config.read_bytes(), lockfile.read_bytes()

        code, records, output = invoke_entry_remove(config, *mode, "@/installed.txt")

        assert code == 2, output
        assert records[0]["type"] == "error"
        assert str(lockfile) in output
        assert (config.read_bytes(), lockfile.read_bytes()) == before
        assert (config.parent / "installed.txt").read_bytes() == b"original"

    @pytest.mark.parametrize("mode", [[], ["--force"], ["--keep-files"]])
    def test_symlinked_selection_is_rejected_before_any_changes(self, config: Path, mode: list[str]) -> None:
        target = config.parent / "installed.txt"
        target.unlink()
        target.symlink_to(config.parent / "unselected")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_remove(config, *mode, "@/docs/shared", "@/installed.txt")

        assert code == 3, output
        assert records[0]["code"] == path_errors.InvalidProjectPath.model_fields["code"].default
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert (config.parent / "docs/shared/guide").read_bytes() == b"guide"
        assert (config.parent / "unselected").read_bytes() == b"unselected"

    @pytest.mark.parametrize("force", [False, True])
    def test_symlink_inside_deleted_tree_cannot_be_bypassed(self, config: Path, force: bool) -> None:
        (config.parent / "docs/shared/link").symlink_to(config.parent / "unselected")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_remove(
            config, *(["--force"] if force else []), "@/installed.txt", "@/docs/shared"
        )

        assert code == 3, output
        assert records[0]["code"] == path_errors.InvalidProjectPath.model_fields["code"].default
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert (config.parent / "installed.txt").read_bytes() == b"original"

    @pytest.mark.parametrize("mode", [[], ["--force"], ["--keep-files"]])
    def test_escaping_path_preserves_selection_and_outside_content(self, config: Path, mode: list[str]) -> None:
        outside = config.parent.parent / "outside"
        outside.write_bytes(b"outside")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_remove(config, *mode, "@/installed.txt", str(outside))

        assert code == 3, output
        assert records[0]["code"] == path_errors.InvalidProjectPath.model_fields["code"].default
        assert outside.read_bytes() == b"outside"
        assert (config.parent / "installed.txt").read_bytes() == b"original"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_forced_removal_cannot_delete_an_obstructing_ancestor(self, config: Path) -> None:
        (config.parent / "docs").rename(config.parent / "outside-selection")
        (config.parent / "docs").write_bytes(b"obstructing parent")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_remove(config, "--force", "@/installed.txt", "@/docs/shared")

        assert code == 3, output
        assert records[0]["type"] == "error"
        assert (config.parent / "docs").read_bytes() == b"obstructing parent"
        assert (config.parent / "installed.txt").read_bytes() == b"original"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_unselected_symlink_does_not_require_inspection(self, config: Path) -> None:
        target = config.parent / "unselected"
        target.unlink()
        target.symlink_to(config.parent / "docs", target_is_directory=True)

        code, _, output = invoke_entry_remove(config, "@/installed.txt")

        assert code == 0, output
        assert target.is_symlink()
        assert (config.parent / "docs/shared/guide").read_bytes() == b"guide"

    def test_cache_and_source_access_are_unused(self, config: Path, mocker: MockerFixture) -> None:
        for name in (
            "vcsinstall.cli.preparation.resolve_cache_root",
            "vcsinstall.runtime.commands.get_hosting",
            "vcsinstall.vcs_clients.GitClient.run",
            "vcsinstall.cache.paths.user_cache_path",
        ):
            mocker.patch(name, side_effect=AssertionError("unexpected source or cache access"))
        result = CliRunner().invoke(
            app, ["--config", str(config), "--cache-dir", str(config / "invalid-cache"), "remove", "@/installed.txt"]
        )

        assert result.exit_code == 0, result.output
        assert not (config.parent / "installed.txt").exists()

    def test_deletion_failure_reports_partial_effects_and_preserves_metadata(
        self, config: Path, mocker: MockerFixture
    ) -> None:
        from vcsinstall.filesystem import FSClient

        original = FSClient.remove
        problem = filesystem_errors.AccessFailed(path=config.parent / "installed.txt", reason="deletion denied")

        def remove(client: FSClient, destination: ProjectPathId) -> Result[None]:
            if destination == "@/installed.txt":
                return Err([problem])
            return original(client, destination)

        mocker.patch.object(FSClient, "remove", autospec=True, side_effect=remove)
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_remove(config, "@/installed.txt", "@/docs/shared")

        assert code == 3, output
        assert [record["code"] for record in records] == [
            runtime_errors.RemovalWriteFailed.model_fields["code"].default,
            filesystem_errors.AccessFailed.model_fields["code"].default,
        ]
        assert records[0]["destination"] == "@/installed.txt"
        assert "inconsistent" in output
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert not (config.parent / "docs/shared").exists()
        assert (config.parent / "installed.txt").read_bytes() == b"original"
        assert not active_workspace.is_set()

    @pytest.mark.parametrize("failed_file", ["custom.toml", "vcsinstall.lock"])
    def test_metadata_write_failures_identify_completed_effects(
        self, config: Path, mocker: MockerFixture, failed_file: str
    ) -> None:
        original = Path.write_bytes
        failure = config.parent / failed_file
        lockfile = config.with_name("vcsinstall.lock")
        before = config.read_bytes(), lockfile.read_bytes()

        def write(path: Path, data: bytes) -> int:
            if path == failure:
                raise PermissionError("project write denied")
            return original(path, data)

        mocker.patch.object(Path, "write_bytes", autospec=True, side_effect=write)

        code, records, output = invoke_entry_remove(config, "@/installed.txt")

        assert code == 3, output
        assert len(records) == 1
        assert records[0]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert records[0]["path"] == str(failure)
        assert "completed project writes" in output
        assert "Installed file changes may already have completed" in output
        assert not (config.parent / "installed.txt").exists()
        assert lockfile.read_bytes() == before[1]
        assert (config.read_bytes() == before[0]) == (failed_file == "custom.toml")
        assert not active_workspace.is_set()

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_success_and_expected_failure_use_shared_protocol(self, config: Path, protocol: str) -> None:
        arguments = ["-p", protocol, "--config", str(config), "remove", "@/installed.txt"]
        success = CliRunner().invoke(app, arguments)
        unknown = CliRunner().invoke(app, arguments)

        assert success.exit_code == 0, success.output
        assert unknown.exit_code == 1, unknown.output
        assert not success.stderr and not unknown.stderr
        assert "@/installed.txt" in success.stdout
        if protocol == "llm":
            assert "--VCSINSTALL-CELL " in success.stdout
            assert "--VCSINSTALL-CELL " in unknown.stdout
        elif protocol == "automation":
            assert json.loads(success.stdout)["operation"] == "remove"
            assert json.loads(unknown.stdout)["type"] == "error"

    def test_argument_separator_accepts_leading_hyphen(self, config: Path, mocker: MockerFixture) -> None:
        declare_entry(config, source="@/dash", destination="@/-guide", files=b"dash")
        (config.parent / "-guide").write_bytes(b"dash")
        mocker.patch("pathlib.Path.cwd", return_value=config.parent)

        code, records, output = invoke_entry_remove(config, "--", "-guide")

        assert code == 0, output
        assert records[0]["destination"] == "@/-guide"
        assert not (config.parent / "-guide").exists()


@pytest.mark.usefixtures("isolated_settings")
class TestAddEntry:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        path = project / "custom.toml"
        path.write_text("# configuration\nversion = 1\n")
        return path

    @pytest.fixture
    def client(self, config: Path, tmp_path: Path) -> GitClient:
        client = create_repository(tmp_path / "source")
        code, _, output = invoke_add(config, tmp_path / "cache", "ci", str(client.path))
        assert code == 0, output
        return client

    @pytest.mark.parametrize(
        "vcs,kind",
        [
            ("git", "file"),
            ("git", "directory"),
            ("git", "root"),
            ("filesystem", "file"),
            ("filesystem", "directory"),
            ("filesystem", "root"),
            ("filesystem", "empty"),
        ],
    )
    def test_installs_exact_destination_and_records_baseline(  # noqa: CCR001 — Verify each backend's content kinds.
        self, config: Path, tmp_path: Path, vcs: str, kind: str
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guides").mkdir()
        (source / "guides" / ".hidden").write_bytes(b"literal\x00bytes\r\n")
        (source / "guides" / "run").write_bytes(b"#!/bin/sh\n")
        (source / "guides" / "run").chmod(0o755)
        (source / "empty").mkdir()
        if vcs == "git":
            create_repository(source)
        code, dependency, output = invoke_add(config, tmp_path / "cache", "--vcs", vcs, "ci", str(source))
        assert code == 0, output
        selected = {"file": "guides/.hidden", "directory": "guides", "root": "", "empty": "empty"}[kind]

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/" + selected, "@/renamed")

        assert code == 0, output
        assert len(records) == 1
        record = records[0]
        assert record["type"] == "installation"
        assert record["operation"] == "add"
        assert record["dependency"] == "ci"
        assert record["destination"] == "@/renamed"
        assert record["source_path"] == "@/" + selected
        assert record["outcome"] == "installed"
        assert record["status"] == "unmodified"
        assert record["revision"] == dependency[0]["revision"]
        assert record["content"] is None
        target = config.parent / "renamed"
        if kind == "file":
            assert target.read_bytes() == b"literal\x00bytes\r\n"
        elif kind == "empty":
            assert not target.exists()
        else:
            directory = target / "guides" if kind == "root" else target
            assert (directory / ".hidden").read_bytes() == b"literal\x00bytes\r\n"
            assert (directory / "run").stat().st_mode & 0o111
        declaration = tomllib.loads(config.read_text())["entries"]
        baseline = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["entries"]
        assert len(declaration) == len(baseline) == 1
        assert declaration[0] == {
            "dependency": "ci",
            "source_path": "@/" + selected,
            "destination": "@/renamed",
        }
        assert baseline[0]["kind"] == ("file" if kind == "file" else "directory")
        if kind == "empty":
            assert baseline[0]["files"] == []
        assert not project_config.is_set()
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()
        assert (tmp_path / "cache").exists() == (vcs == "git")

    @pytest.mark.parametrize("form", ["identifier", "relative", "absolute"])
    def test_destination_normalization_uses_invocation_directory(
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture, form: str
    ) -> None:
        invocation = config.parent / "nested"
        invocation.mkdir()
        mocker.patch("pathlib.Path.cwd", return_value=invocation)
        destination = {
            "identifier": " @/nested/unused/../installed ",
            "relative": " ./unused/../installed ",
            "absolute": str(invocation / "installed"),
        }[form]

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", destination)

        assert code == 0, output
        assert records[0]["destination"] == "@/nested/installed"
        assert (invocation / "installed").read_bytes() == b"original\n"

    @pytest.mark.parametrize("name", ["notes%20+draft#1?.md", "@/notes", "Заметки.md", "-guide"])
    def test_preserves_literal_source_names(self, config: Path, tmp_path: Path, name: str) -> None:
        source = tmp_path / "source"
        path = source / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b"literal")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/" + name, "@/installed")

        assert code == 0, output
        assert records[0]["source_path"] == "@/" + name
        assert (config.parent / "installed").read_bytes() == b"literal"

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_results_and_conflicts_use_shared_protocol(
        self, config: Path, client: GitClient, tmp_path: Path, protocol: str
    ) -> None:
        arguments = [
            "-p",
            protocol,
            "--config",
            str(config),
            "--cache-dir",
            str(tmp_path / "cache"),
            "add",
            "ci@/guide.txt",
            "@/installed",
        ]
        success = CliRunner().invoke(app, arguments)
        duplicate = CliRunner().invoke(app, arguments)

        assert success.exit_code == 0, success.output
        assert duplicate.exit_code == 1, duplicate.output
        assert not success.stderr and not duplicate.stderr
        assert "--redefine" in duplicate.stdout
        assert "@/installed" in duplicate.stdout
        if protocol == "llm":
            assert "--VCSINSTALL-CELL " in success.stdout
            assert "--VCSINSTALL-CELL " in duplicate.stdout
        if protocol == "automation":
            assert json.loads(success.stdout)["operation"] == "add"
            assert json.loads(duplicate.stdout)["type"] == "error"

    @pytest.mark.parametrize(
        "arguments",
        [
            [],
            ["ci@/"],
            ["ci@/", "@/one", "@/two"],
            ["--vcs", "git", "ci@/", "@/one"],
            ["--branch", "trunk", "ci@/", "@/one"],
            ["--tag", "v1", "ci@/", "@/one"],
            ["--rev", "1234", "ci@/", "@/one"],
            ["--replace", "ci@/", "@/one"],
            ["git+https://example.test/shared", "@/one"],
            ["ci@/../escape", "@/one"],
        ],
    )
    def test_invalid_command_shape_precedes_project_loading(self, tmp_path: Path, arguments: list[str]) -> None:
        result = CliRunner().invoke(app, ["--config", str(tmp_path / "missing"), "add", *arguments])

        assert result.exit_code == 2, result.output
        assert not tuple(tmp_path.iterdir())
        assert not active_workspace.is_set()

    @pytest.mark.parametrize("state", ["unknown", "missing_lockfile", "missing_lock", "repository", "selector", "vcs"])
    def test_invalid_selection_fails_before_acquisition(  # noqa: CCR001 — Prepare each independent invalid lock state.
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture, state: str
    ) -> None:
        lockfile = config.with_name("vcsinstall.lock")
        source = "missing@/guide.txt" if state == "unknown" else "ci@/guide.txt"
        if state == "missing_lockfile":
            lockfile.unlink()
        elif state == "missing_lock":
            lockfile.write_text("version = 1\n")
        elif state in ("repository", "selector", "vcs"):
            data = tomllib.loads(config.read_text())
            if state == "repository":
                data["dependencies"]["ci"]["repository"] = "other-source"
            elif state == "selector":
                data["dependencies"]["ci"]["selector"] = {"kind": "branch", "value": "other"}
            else:
                data["dependencies"]["ci"] = {"vcs": "filesystem", "repository": str(client.path)}
            config.write_text(tomli_w.dumps(data))
        before = config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None
        acquire = mocker.patch(
            "vcsinstall.hosting.adapters.git.GitHostingAdapter.acquire",
            side_effect=AssertionError("acquisition before validation"),
        )

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "--allow-outdated", source, "@/installed")

        assert code == (1 if state == "unknown" else 3), output
        if state != "unknown":
            assert records[0]["code"] == workspace_errors.DependencyLockRequired.model_fields["code"].default
            assert "install" in str(records[0]["content"])
            assert str(lockfile) in str(records[0]["content"])
        acquire.assert_not_called()
        assert (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None) == before
        assert not (config.parent / "installed").exists()

    @pytest.mark.parametrize(
        "destination", ["@/", "@/custom.toml", "@/vcsinstall.lock", "@/custom.toml/child", "../outside"]
    )
    def test_reserved_or_escaping_destinations_preserve_project(
        self, config: Path, client: GitClient, tmp_path: Path, destination: str
    ) -> None:
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()
        code, _, _ = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", destination)

        assert code != 0
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    @pytest.mark.parametrize("existing", [b"original\n", b"local change"])
    def test_unmanaged_file_must_match_selected_source(
        self, config: Path, client: GitClient, tmp_path: Path, existing: bytes
    ) -> None:
        installed = config.parent / "installed"
        installed.write_bytes(existing)
        os.utime(installed, ns=(1, 1))
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", "@/installed")

        assert code == (0 if existing == b"original\n" else 3), output
        assert installed.read_bytes() == existing
        assert installed.stat().st_mtime_ns == 1
        if code:
            assert records[0]["code"] == runtime_errors.LocalConflict.model_fields["code"].default
            assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    @pytest.mark.parametrize("change", ["missing", "local", "addition", "unchanged"])
    def test_redefinition_checks_complete_local_tree_and_restores_missing_files(  # noqa: CCR001 — Local tree cases.
        self, config: Path, client: GitClient, tmp_path: Path, change: str
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_entry_add(config, cache, "ci@/", "@/installed")[0] == 0
        target = config.parent / "installed" / "guide.txt"
        if change == "missing":
            target.unlink()
        elif change == "local":
            target.write_bytes(b"edited")
        elif change == "addition":
            (target.parent / "generated").write_bytes(b"extra")
        else:
            os.utime(target, ns=(1, 1))
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, cache, "--redefine", "ci@/", "@/installed")

        assert code == (3 if change in ("local", "addition") else 0), output
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        if code == 0:
            assert records[0]["outcome"] == ("unchanged" if change == "unchanged" else "installed")
            assert target.read_bytes() == b"original\n"
            if change == "unchanged":
                assert target.stat().st_mtime_ns == 1

    def test_redefinition_can_change_dependency_and_file_kind(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/unselected")[0] == 0
        original = (config.parent / "unselected").read_bytes()
        source = tmp_path / "other"
        source.mkdir()
        (source / "new").write_bytes(b"new dependency")
        assert invoke_add(config, cache, "other", source.as_uri())[0] == 0
        locks = tomllib.loads(config.with_name("vcsinstall.lock").read_text())["dependencies"]

        code, records, output = invoke_entry_add(config, cache, "--redefine", "other@/", "@/installed")

        assert code == 0, output
        assert records[0]["dependency"] == "other"
        assert (config.parent / "installed" / "new").read_bytes() == b"new dependency"
        assert (config.parent / "unselected").read_bytes() == original
        assert tomllib.loads(config.with_name("vcsinstall.lock").read_text())["dependencies"] == locks
        assert len(tomllib.loads(config.read_text())["entries"]) == 2
        code, _, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/installed")
        assert code == 0, output
        assert (config.parent / "installed").read_bytes() == b"original\n"

    @pytest.mark.parametrize("destination", ["@/installed", "@/installed/child", "@/parent"])
    def test_duplicate_or_overlapping_entries_fail_before_acquisition(
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture, destination: str
    ) -> None:
        existing = "@/parent/child" if destination == "@/parent" else "@/installed"
        assert invoke_entry_add(config, tmp_path / "cache", "ci@/", existing)[0] == 0
        acquire = mocker.patch("vcsinstall.hosting.adapters.git.GitHostingAdapter.acquire")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", destination)

        assert code == 1, output
        assert destination in str(records[0]["content"])
        acquire.assert_not_called()
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_unchanged_redefinition_preserves_order_and_project_bytes(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        for path in ("@/first", "@/second"):
            assert invoke_entry_add(config, cache, "ci@/guide.txt", path)[0] == 0
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/first")

        assert code == 0, output
        assert records[0]["outcome"] == "unchanged"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_unselected_stale_mapping_and_baseline_remain_untouched(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_entry_add(config, cache, "ci@/", "@/unselected")[0] == 0
        lockfile = config.with_name("vcsinstall.lock")
        before = tomllib.loads(lockfile.read_text())
        data = tomllib.loads(config.read_text())
        data["entries"][0]["destination"] = "@/unselected/changed-mapping"
        config.write_text(tomli_w.dumps(data))

        code, _, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/new")

        assert code == 0, output
        assert tomllib.loads(config.read_text())["entries"][0] == data["entries"][0]
        after = tomllib.loads(lockfile.read_text())
        assert after["entries"][0] == before["entries"][0]
        assert after["dependencies"] == before["dependencies"]
        assert (config.parent / "unselected" / "guide.txt").read_bytes() == b"original\n"
        assert (config.parent / "new").read_bytes() == b"original\n"

    @pytest.mark.parametrize("declared", [False, True])
    def test_unselected_symlinked_destinations_do_not_block_add(
        self, config: Path, client: GitClient, tmp_path: Path, declared: bool
    ) -> None:
        mapping = {"dependency": "ci", "source_path": "@/guide.txt", "destination": "@/unselected/installed"}
        if declared:
            configuration = tomllib.loads(config.read_text())
            configuration["entries"] = [mapping]
            config.write_text(tomli_w.dumps(configuration))
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        baseline = {**mapping, "kind": "absent"}
        recorded["entries"] = [baseline]
        lockfile.write_text(tomli_w.dumps(recorded))
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "installed").write_bytes(b"keep")
        (config.parent / "unselected").symlink_to(outside, target_is_directory=True)

        code, _, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", "@/new")

        assert code == 0, output
        assert tomllib.loads(lockfile.read_text())["entries"][0] == baseline
        assert (outside / "installed").read_bytes() == b"keep"
        assert (config.parent / "new").read_bytes() == b"original\n"

    @pytest.mark.parametrize("selector", ["branch", "tag"])
    @pytest.mark.parametrize("same_content", [False, True])
    def test_freshness_permission_keeps_locked_revision_and_unselected_entries(
        self, config: Path, client: GitClient, tmp_path: Path, selector: str, same_content: bool
    ) -> None:
        cache = tmp_path / "cache"
        old_revision = client.resolve_commit("HEAD").unwrap()
        if selector == "tag":
            client.create_tag("release").unwrap()
            assert invoke_add(config, cache, "--redefine", "--tag", "release", "ci", str(client.path))[0] == 0
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/unselected")[0] == 0
        if not same_content:
            (client.path / "guide.txt").write_bytes(b"new source")
        client.run(["commit", "--allow-empty", "-am", "Advance source"]).unwrap()
        current_revision = client.resolve_commit("HEAD").unwrap()
        if selector == "tag":
            client.run(["tag", "--force", "release", current_revision]).unwrap()
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/installed")

        assert code == 3, output
        assert records[0]["code"] == runtime_errors.OutdatedDependency.model_fields["code"].default
        assert old_revision in output and current_revision in output
        assert "--allow-outdated" in output and "dependency update ci" in output
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        code, records, output = invoke_entry_add(config, cache, "--allow-outdated", "ci@/guide.txt", "@/installed")
        assert code == 0, output
        assert records[0]["revision"] == old_revision
        assert (config.parent / "installed").read_bytes() == b"original\n"
        assert (config.parent / "unselected").read_bytes() == b"original\n"
        assert (
            tomllib.loads(config.with_name("vcsinstall.lock").read_text())["dependencies"]["ci"]["revision"]
            == old_revision
        )

    def test_allow_outdated_does_not_bypass_failed_refresh(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        client.path.rename(tmp_path / "unavailable")
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, _, output = invoke_entry_add(
            config, tmp_path / "cache", "--allow-outdated", "ci@/guide.txt", "@/installed"
        )

        assert code == 3, output
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert not (config.parent / "installed").exists()

    def test_omitted_git_selector_retains_locked_branch_and_relative_repository(
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        data = tomllib.loads(config.read_text())
        data["dependencies"]["ci"].pop("selector")
        data["dependencies"]["ci"]["repository"] = "../source"
        config.write_text(tomli_w.dumps(data))
        client.create_branch("new-default", checkout=True).unwrap()
        (client.path / "guide.txt").write_bytes(b"other branch")
        client.commit("Change advertised default", all_tracked=True).unwrap()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        mocker.patch("pathlib.Path.cwd", return_value=elsewhere)
        declaration = config.read_bytes()

        code, records, output = invoke_entry_add(config, tmp_path / "fresh-cache", "ci@/guide.txt", "@/installed")

        assert code == 0, output
        assert records[0]["ref"] == "trunk"
        assert (config.parent / "installed").read_bytes() == b"original\n"
        assert tomllib.loads(config.read_text())["dependencies"] == tomllib.loads(declaration.decode())["dependencies"]

    def test_pinned_revision_uses_available_cache_without_refresh(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        revision = client.resolve_commit("HEAD").unwrap()
        cache = tmp_path / "cache"
        assert invoke_add(config, cache, "--redefine", "--rev", revision, "ci", str(client.path))[0] == 0
        client.path.rename(tmp_path / "unavailable")

        code, records, output = invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")

        assert code == 0, output
        assert records[0]["revision"] == revision
        assert records[0]["ref_kind"] == "revision"

    @pytest.mark.parametrize("allow_outdated", [False, True])
    def test_absent_source_path_preserves_project(
        self, config: Path, client: GitClient, tmp_path: Path, allow_outdated: bool
    ) -> None:
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()
        code, records, output = invoke_entry_add(
            config, tmp_path / "cache", *(["--allow-outdated"] if allow_outdated else []), "ci@/missing", "@/installed"
        )

        assert code == 3, output
        assert records[0]["code"] == runtime_errors.SourcePathAbsent.model_fields["code"].default
        assert records[0]["source_path"] == "@/missing"
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_integrity_mismatch_cannot_rewrite_existing_baseline(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/installed")[0] == 0
        lock = config.with_name("vcsinstall.lock")
        data = tomllib.loads(lock.read_text())
        data["entries"][0]["hash"] = hashes.hash_file(b"different baseline")
        lock.write_text(tomli_w.dumps(data))
        before = config.read_bytes(), lock.read_bytes()

        code, records, output = invoke_entry_add(config, cache, "--redefine", "ci@/guide.txt", "@/installed")

        assert code == 3, output
        assert records[0]["code"] == runtime_errors.SourceIntegrityMismatch.model_fields["code"].default
        assert (config.read_bytes(), lock.read_bytes()) == before
        assert (config.parent / "installed").read_bytes() == b"original\n"

    @pytest.mark.parametrize("location", ["destination", "parent", "source"])
    def test_symbolic_links_are_rejected_without_mutation(self, config: Path, tmp_path: Path, location: str) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide").write_bytes(b"source")
        assert invoke_add(config, tmp_path / "cache", "ci", source.as_uri())[0] == 0
        if location == "source":
            (source / "link").symlink_to(source / "guide")
        else:
            (config.parent / "link").symlink_to(tmp_path, target_is_directory=True)
        destination = (
            "@/link/installed" if location == "parent" else "@/link" if location == "destination" else "@/installed"
        )
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(
            config, tmp_path / "cache", "--allow-outdated", "ci@/guide", destination
        )

        assert code == 3, output
        assert records[0]["code"] == path_errors.InvalidProjectPath.model_fields["code"].default
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before

    def test_copy_failure_reports_partial_installation_and_keeps_project_records(
        self, config: Path, client: GitClient, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        from vcsinstall.sources.adapters.git import GitSourceAdapter

        problem = filesystem_errors.AccessFailed(path=config.parent / "installed", reason="write denied")
        mocker.patch.object(GitSourceAdapter, "copy_file", return_value=Err([problem]))
        before = config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_entry_add(config, tmp_path / "cache", "ci@/guide.txt", "@/installed")

        assert code == 3, output
        assert [record["code"] for record in records] == [
            runtime_errors.InstallationWriteFailed.model_fields["code"].default,
            filesystem_errors.AccessFailed.model_fields["code"].default,
        ]
        assert (config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()) == before
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()


@pytest.mark.usefixtures("isolated_settings")
class TestInstallEntries:
    @pytest.fixture(autouse=True)
    def initialize(self, isolated_settings: None) -> None:
        settings.initialize(
            label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
        )

    @pytest.fixture
    def client(self, tmp_path: Path) -> GitClient:
        client = create_repository(tmp_path / "source")
        (client.path / "docs/nested").mkdir(parents=True)
        (client.path / "docs/a").write_bytes(b"first")
        (client.path / "docs/nested/b").write_bytes(b"second")
        client.add(".").unwrap()
        client.commit("Add directory content").unwrap()
        return client

    @pytest.fixture
    def config(self, tmp_path: Path, client: GitClient) -> Path:
        project = tmp_path / "project"
        project.mkdir()
        config = project / "custom.toml"
        definition = {"vcs": "git", "repository": str(client.path), "selector": {"kind": "branch", "value": "trunk"}}
        config.write_text(
            tomli_w.dumps(
                {
                    "version": 1,
                    "dependencies": {"z-empty": definition, "ci": definition},
                    "entries": [
                        {"dependency": "ci", "source_path": "@/guide.txt", "destination": "@/z-guide.txt"},
                        {"dependency": "ci", "source_path": "@/docs", "destination": "@/a-docs"},
                    ],
                }
            )
        )
        return config

    def test_installs_complete_project_once_and_reports_ordered_records(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        original = config.read_bytes()
        revision = client.run(["rev-parse", "HEAD"]).unwrap().decode().strip()

        code, records, output = invoke_install(config, tmp_path / "cache")

        assert code == 0, output
        assert config.read_bytes() == original
        assert (config.parent / "z-guide.txt").read_bytes() == b"original\n"
        assert (config.parent / "a-docs/a").read_bytes() == b"first"
        assert (config.parent / "a-docs/nested/b").read_bytes() == b"second"
        assert [record["type"] for record in records] == ["dependency", "dependency", "installation", "installation"]
        assert [record["dependency"] for record in records] == ["ci", "z-empty", "ci", "ci"]
        assert [record["destination"] for record in records[2:]] == ["@/a-docs", "@/z-guide.txt"]
        for record in records:
            assert record["operation"] == "install"
            assert record["outcome"] == "installed"
            assert record["revision"] == revision
            assert record["ref_kind"] == "branch"
            assert record["ref"] == "trunk"
            assert record["repository"] == str(client.path)
            assert record["content"] is None
            assert "previous_revision" not in record
        assert all(record["status"] == "unmodified" for record in records[2:])
        locked = tomllib.loads(config.with_name("vcsinstall.lock").read_text())
        assert set(locked["dependencies"]) == {"ci", "z-empty"}
        directory = next(entry for entry in locked["entries"] if entry["kind"] == "directory")
        assert [file["path"] for file in directory["files"]] == ["@/a", "@/nested/b"]
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()
        assert not project_config.is_set()

    @pytest.mark.parametrize("locked", [False, True])
    def test_satisfied_installation_needs_no_source_client_or_cache_access(
        self, config: Path, tmp_path: Path, mocker: MockerFixture, locked: bool
    ) -> None:
        assert invoke_install(config, tmp_path / "cache")[0] == 0
        lockfile = config.with_name("vcsinstall.lock")
        lockfile.write_bytes(b"# preserve lock formatting\n" + lockfile.read_bytes())
        before = config.read_bytes(), lockfile.read_bytes()
        original = (config.parent / "z-guide.txt").stat()
        mocker.patch(
            "vcsinstall.runtime.installations.get_hosting", side_effect=AssertionError("unexpected acquisition")
        )
        mocker.patch.object(GitClient, "run", side_effect=AssertionError("unexpected Git access"))

        code, records, output = invoke_install(config, tmp_path / "unused-cache", *(("--locked",) if locked else ()))

        assert code == 0, output
        assert all(record["outcome"] == "unchanged" for record in records)
        assert (config.read_bytes(), lockfile.read_bytes()) == before
        current = (config.parent / "z-guide.txt").stat()
        assert (current.st_ino, current.st_mtime_ns) == (original.st_ino, original.st_mtime_ns)
        assert not (tmp_path / "unused-cache").exists()

    def test_new_entries_and_restoration_use_existing_revision_without_refresh(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_install(config, cache)[0] == 0
        lockfile = config.with_name("vcsinstall.lock")
        revision = tomllib.loads(lockfile.read_text())["dependencies"]["ci"]["revision"]
        (client.path / "guide.txt").write_bytes(b"advanced")
        client.add(".").unwrap()
        client.commit("Advance source branch").unwrap()
        declaration = tomllib.loads(config.read_text())
        declaration["entries"].append(
            {"dependency": "ci", "source_path": "@/guide.txt", "destination": "@/new-guide.txt"}
        )
        config.write_text(tomli_w.dumps(declaration))
        (config.parent / "z-guide.txt").unlink()
        client.path.rename(tmp_path / "offline-source")

        code, records, output = invoke_install(config, cache)

        assert code == 0, output
        assert all(record["revision"] == revision for record in records)
        assert (config.parent / "new-guide.txt").read_bytes() == b"original\n"
        assert (config.parent / "z-guide.txt").read_bytes() == b"original\n"
        assert next(record for record in records if record.get("destination") == "@/a-docs")["outcome"] == "unchanged"
        assert records[1]["outcome"] == "unchanged"

    def test_locked_restoration_rebuilds_cache_without_changing_lock_bytes(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        assert invoke_install(config, cache)[0] == 0
        lockfile = config.with_name("vcsinstall.lock")
        lockfile.write_bytes(b"# byte preservation\n" + lockfile.read_bytes())
        before = lockfile.read_bytes()
        (config.parent / "z-guide.txt").unlink()
        (config.parent / "a-docs/nested/b").unlink()
        unchanged = (config.parent / "a-docs/a").stat()
        cache.rename(tmp_path / "old-cache")
        (client.path / "guide.txt").write_bytes(b"advanced")
        client.add(".").unwrap()
        client.commit("Advance source").unwrap()

        code, records, output = invoke_install(config, cache, "--locked")

        assert code == 0, output
        assert lockfile.read_bytes() == before
        assert (config.parent / "z-guide.txt").read_bytes() == b"original\n"
        assert (config.parent / "a-docs/nested/b").read_bytes() == b"second"
        assert (config.parent / "a-docs/a").stat().st_mtime_ns != unchanged.st_mtime_ns
        assert records[0]["outcome"] == "installed"
        assert records[1]["outcome"] == "unchanged"

    @pytest.mark.parametrize(
        "change",
        [
            "lockfile",
            "dependency",
            "baseline",
            "source_path",
            "destination",
            "repository",
            "selector",
            "obsolete_dependency",
            "obsolete_entry",
            "reassigned",
        ],
    )
    def test_locked_requires_a_complete_match_before_source_access(  # noqa: CCR001 — Independent stale-state cases.
        self, config: Path, tmp_path: Path, mocker: MockerFixture, change: str
    ) -> None:
        assert invoke_install(config, tmp_path / "cache")[0] == 0
        lockfile = config.with_name("vcsinstall.lock")
        declaration = tomllib.loads(config.read_text())
        recorded = tomllib.loads(lockfile.read_text())
        if change == "lockfile":
            lockfile.unlink()
        elif change == "dependency":
            declaration["dependencies"]["new"] = declaration["dependencies"]["ci"]
        elif change == "baseline":
            recorded["entries"].pop()
        elif change in ("source_path", "destination"):
            declaration["entries"][0][change] = "@/different"
        elif change == "repository":
            declaration["dependencies"]["ci"]["repository"] = "../other"
        elif change == "selector":
            declaration["dependencies"]["ci"]["selector"]["value"] = "other"
        elif change == "obsolete_dependency":
            declaration["dependencies"].pop("z-empty")
        elif change == "obsolete_entry":
            declaration["entries"].pop()
        elif change == "reassigned":
            declaration["entries"][0]["dependency"] = "z-empty"
        config.write_text(tomli_w.dumps(declaration))
        if change != "lockfile":
            lockfile.write_text(tomli_w.dumps(recorded))
        before = config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None
        mocker.patch(
            "vcsinstall.runtime.installations.get_hosting", side_effect=AssertionError("unexpected acquisition")
        )

        code, records, output = invoke_install(config, tmp_path / "unused-cache", "--locked")

        assert code == 3, output
        assert all(record["type"] == "error" for record in records)
        assert str(lockfile) in output
        assert (config.read_bytes(), lockfile.read_bytes() if lockfile.exists() else None) == before
        assert (config.parent / "z-guide.txt").read_bytes() == b"original\n"
        assert (config.parent / "a-docs/nested/b").read_bytes() == b"second"
        assert not (tmp_path / "unused-cache").exists()

    def test_reconciles_obsolete_records_and_new_dependencies_without_deleting_removed_content(
        self, config: Path, tmp_path: Path
    ) -> None:
        assert invoke_install(config, tmp_path / "cache")[0] == 0
        declaration = tomllib.loads(config.read_text())
        declaration["dependencies"].pop("z-empty")
        declaration["entries"] = declaration["entries"][:1]
        directory = tmp_path / "files"
        directory.mkdir()
        (directory / "new").write_bytes(b"filesystem content")
        declaration["dependencies"]["local"] = {"vcs": "filesystem", "repository": str(directory)}
        declaration["entries"].append({"dependency": "local", "source_path": "@/new", "destination": "@/new"})
        config.write_text("# retain configuration exactly\n" + tomli_w.dumps(declaration))
        original = config.read_bytes()
        (config.parent / "a-docs/local-added").write_bytes(b"unmanaged after removal")
        (config.parent / "a-docs/a").write_bytes(b"local changes")

        code, records, output = invoke_install(config, tmp_path / "cache")

        assert code == 0, output
        assert config.read_bytes() == original
        recorded = tomllib.loads(config.with_name("vcsinstall.lock").read_text())
        assert set(recorded["dependencies"]) == {"ci", "local"}
        assert {entry["destination"] for entry in recorded["entries"]} == {"@/z-guide.txt", "@/new"}
        assert (config.parent / "a-docs/a").read_bytes() == b"local changes"
        assert (config.parent / "a-docs/local-added").read_bytes() == b"unmanaged after removal"
        assert (config.parent / "new").read_bytes() == b"filesystem content"
        assert records[0]["outcome"] == "installed"
        assert (
            next(record for record in records if record.get("destination") == "@/z-guide.txt")["outcome"]
            == "unchanged"
        )

    @pytest.mark.parametrize("local", [b"original\n", b"new\n", b"conflict\n"])
    def test_changed_binding_protects_previous_baseline_or_accepts_complete_source_content(
        self, config: Path, client: GitClient, tmp_path: Path, local: bytes
    ) -> None:
        assert invoke_install(config, tmp_path / "cache")[0] == 0
        client.run(["checkout", "-b", "replacement"]).unwrap()
        (client.path / "guide.txt").write_bytes(b"new\n")
        (client.path / "docs/new").write_bytes(b"new child")
        client.add(".").unwrap()
        client.commit("Changed binding content").unwrap()
        declaration = tomllib.loads(config.read_text())
        declaration["dependencies"]["ci"]["selector"]["value"] = "replacement"
        config.write_text(tomli_w.dumps(declaration))
        target = config.parent / "z-guide.txt"
        target.write_bytes(local)
        before = target.stat(), config.with_name("vcsinstall.lock").read_bytes()

        code, records, output = invoke_install(config, tmp_path / "cache")

        if local == b"conflict\n":
            assert code == 3, output
            assert any(
                record["code"] == runtime_errors.LocalConflict.model_fields["code"].default for record in records
            )
            assert target.read_bytes() == local
            assert config.with_name("vcsinstall.lock").read_bytes() == before[1]
            assert not (config.parent / "a-docs/new").exists()
        else:
            assert code == 0, output
            assert target.read_bytes() == b"new\n"
            assert (config.parent / "a-docs/new").read_bytes() == b"new child"
            if local == b"new\n":
                assert target.stat().st_mtime_ns == before[0].st_mtime_ns

    @pytest.mark.parametrize("change", ["source_path", "dependency"])
    def test_changed_entry_mapping_uses_prior_destination_baseline(
        self, config: Path, tmp_path: Path, change: str
    ) -> None:
        assert invoke_install(config, tmp_path / "cache")[0] == 0
        declaration = tomllib.loads(config.read_text())
        declaration["entries"][0][change] = "@/docs/a" if change == "source_path" else "z-empty"
        config.write_text(tomli_w.dumps(declaration))

        code, records, output = invoke_install(config, tmp_path / "cache")

        assert code == 0, output
        assert (config.parent / "z-guide.txt").read_bytes() == (b"first" if change == "source_path" else b"original\n")
        recorded = tomllib.loads(config.with_name("vcsinstall.lock").read_text())
        baseline = next(entry for entry in recorded["entries"] if entry["destination"] == "@/z-guide.txt")
        assert baseline[change] == declaration["entries"][0][change]
        assert records[-1]["outcome"] == "installed"

    @pytest.mark.parametrize("local_change", ["edited", "addition", "obstruction"])
    def test_restoration_never_hides_existing_local_conflicts(
        self, config: Path, tmp_path: Path, local_change: str
    ) -> None:
        assert invoke_install(config, tmp_path / "cache")[0] == 0
        before = config.with_name("vcsinstall.lock").read_bytes()
        (config.parent / "z-guide.txt").unlink()
        if local_change == "edited":
            (config.parent / "a-docs/a").write_bytes(b"local edit")
        elif local_change == "addition":
            (config.parent / "a-docs/.ignored").write_bytes(b"local addition")
        else:
            (config.parent / "a-docs/nested/b").unlink()
            (config.parent / "a-docs/nested").rmdir()
            (config.parent / "a-docs/nested").write_bytes(b"obstruction")

        code, records, output = invoke_install(config, tmp_path / "cache", "--locked")

        assert code == 3, output
        assert any(record["code"] == runtime_errors.LocalConflict.model_fields["code"].default for record in records)
        assert not (config.parent / "z-guide.txt").exists()
        assert config.with_name("vcsinstall.lock").read_bytes() == before

    def test_source_integrity_failure_preserves_expected_hash_and_all_installed_content(
        self, config: Path, tmp_path: Path
    ) -> None:
        assert invoke_install(config, tmp_path / "cache")[0] == 0
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        baseline = next(entry for entry in recorded["entries"] if entry["kind"] == "file")
        baseline["hash"] = hashes.hash_file(b"untrusted source bytes")
        lockfile.write_text(tomli_w.dumps(recorded))
        before = lockfile.read_bytes()
        (config.parent / "z-guide.txt").unlink()
        (config.parent / "a-docs/nested/b").unlink()

        code, records, output = invoke_install(config, tmp_path / "cache", "--locked")

        assert code == 3, output
        assert any(
            record["code"] == runtime_errors.SourceIntegrityMismatch.model_fields["code"].default for record in records
        )
        assert lockfile.read_bytes() == before
        assert not (config.parent / "z-guide.txt").exists()
        assert not (config.parent / "a-docs/nested/b").exists()

    def test_prepares_every_dependency_before_writing_files_or_lockfile(self, config: Path, tmp_path: Path) -> None:
        declaration = tomllib.loads(config.read_text())
        declaration["dependencies"]["z-empty"] = {"vcs": "filesystem", "repository": str(tmp_path / "unavailable")}
        config.write_text(tomli_w.dumps(declaration))
        original = config.read_bytes()

        code, records, output = invoke_install(config, tmp_path / "cache")

        assert code == 3, output
        assert records[0]["dependency"] == "z-empty"
        assert config.read_bytes() == original
        assert not config.with_name("vcsinstall.lock").exists()
        assert list(config.parent.iterdir()) == [config]

    def test_missing_initial_source_path_is_not_recorded_as_absence(self, config: Path, tmp_path: Path) -> None:
        declaration = tomllib.loads(config.read_text())
        declaration["entries"][0]["source_path"] = "@/unavailable"
        config.write_text(tomli_w.dumps(declaration))

        code, records, output = invoke_install(config, tmp_path / "cache")

        assert code == 3, output
        assert any(
            record["code"] == runtime_errors.SourcePathAbsent.model_fields["code"].default for record in records
        )
        assert not config.with_name("vcsinstall.lock").exists()
        assert list(config.parent.iterdir()) == [config]

    @pytest.mark.parametrize("arguments", [["ci"], ["@/entry"], ["--vcs", "git"], ["--branch", "trunk"], ["--force"]])
    def test_rejects_arguments_and_source_selection_options_before_project_loading(
        self, tmp_path: Path, arguments: list[str]
    ) -> None:
        result = CliRunner().invoke(app, ["--config", str(tmp_path / "missing"), "install", *arguments])

        assert result.exit_code == 2, result.output
        assert not tuple(tmp_path.iterdir())

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_protocol_delivery_and_context_restoration(self, config: Path, tmp_path: Path, protocol: str) -> None:
        result = CliRunner().invoke(
            app, ["-p", protocol, "--config", str(config), "--cache-dir", str(tmp_path / "cache"), "install"]
        )

        assert result.exit_code == 0, result.output
        assert not result.stderr
        assert "install" in result.stdout
        assert "\x1b" not in result.stdout
        if protocol == "llm":
            assert "--VCSINSTALL-CELL " in result.stdout
        assert not active_workspace.is_set()
        assert not active_cache_root.is_set()
        assert not project_config.is_set()

    def test_discovers_configuration_and_resolves_cache_relative_to_invocation(
        self, config: Path, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        declaration = tomllib.loads(config.read_text())
        for definition in declaration["dependencies"].values():
            definition["repository"] = "../source"
        config.write_text(tomli_w.dumps(declaration))
        config = config.rename(config.with_name("vcsinstall.toml"))
        invocation = config.parent / "tools"
        invocation.mkdir()
        mocker.patch("pathlib.Path.cwd", return_value=invocation)

        result = CliRunner().invoke(app, ["--cache-dir", "../selected-cache", "install"])

        assert result.exit_code == 0, result.output
        assert (config.parent / "selected-cache/repos/git").is_dir()
        assert (config.parent / "z-guide.txt").read_bytes() == b"original\n"

    @pytest.mark.parametrize("locked", [False, True])
    def test_empty_project_requires_existing_lock_only_in_locked_mode(self, tmp_path: Path, locked: bool) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text("version = 1\n")

        code, records, output = invoke_install(config, tmp_path / "cache", *(("--locked",) if locked else ()))

        assert code == (3 if locked else 0), output
        if locked:
            assert records[0]["code"] == workspace_errors.LockfileRequired.model_fields["code"].default
            assert not config.with_name("vcsinstall.lock").exists()
        else:
            assert not records
            assert tomllib.loads(config.with_name("vcsinstall.lock").read_text()) == {
                "version": 1,
                "dependencies": {},
                "entries": [],
            }
        assert not (tmp_path / "cache").exists()

    def test_filesystem_source_is_only_needed_when_locked_content_requires_restoration(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "guide").write_bytes(b"live content")
        (source / "outside-selection").write_bytes(b"original")
        project = tmp_path / "project"
        project.mkdir()
        config = project / "vcsinstall.toml"
        config.write_text(
            tomli_w.dumps(
                {
                    "version": 1,
                    "dependencies": {"local": {"vcs": "filesystem", "repository": "../source"}},
                    "entries": [{"dependency": "local", "source_path": "@/guide", "destination": "@/guide"}],
                }
            )
        )
        cache = tmp_path / "unused-cache"
        assert invoke_install(config, cache)[0] == 0
        lockfile = project / "vcsinstall.lock"
        before = lockfile.read_bytes()
        (source / "outside-selection").write_bytes(b"changed")

        code, records, output = invoke_install(config, cache, "--locked")

        assert code == 0, output
        assert all(record["outcome"] == "unchanged" for record in records)
        (project / "guide").unlink()

        code, records, output = invoke_install(config, cache, "--locked")

        assert code == 3, output
        assert records[-1]["code"] == source_errors.RevisionMismatch.model_fields["code"].default
        assert lockfile.read_bytes() == before
        assert not (project / "guide").exists()
        assert not cache.exists()

    @pytest.mark.parametrize("kind", ["directory", "absent"])
    @pytest.mark.parametrize("existing", [False, True])
    def test_zero_file_baselines_preserve_kind_and_bytes_without_source_access(
        self, tmp_path: Path, kind: str, existing: bool
    ) -> None:
        config = tmp_path / "vcsinstall.toml"
        definition = {"vcs": "filesystem", "repository": "../unavailable"}
        entry = {"dependency": "local", "source_path": "@/empty", "destination": "@/empty"}
        config.write_text(tomli_w.dumps({"version": 1, "dependencies": {"local": definition}, "entries": [entry]}))
        baseline: dict[str, object] = dict(entry, kind=kind)
        if kind == "directory":
            baseline.update(hash=hashes.hash_files({}), files=[])
        lockfile = config.with_name("vcsinstall.lock")
        lockfile.write_text(
            tomli_w.dumps(
                {
                    "version": 1,
                    "dependencies": {"local": dict(definition, revision="sha256:" + "1" * 64)},
                    "entries": [baseline],
                }
            )
        )
        if existing:
            (tmp_path / "empty/nested").mkdir(parents=True)
        before = lockfile.read_bytes()

        code, records, output = invoke_install(config, tmp_path / "cache", "--locked")

        assert code == 0, output
        assert all(record["outcome"] == "unchanged" for record in records)
        assert lockfile.read_bytes() == before
        assert (tmp_path / "empty").exists() == existing
        assert not (tmp_path / "cache").exists()

    def test_omitted_git_selector_preserves_the_inferred_branch_and_relative_locked_locator(
        self, config: Path, client: GitClient, tmp_path: Path
    ) -> None:
        declaration = tomllib.loads(config.read_text())
        declaration["dependencies"]["ci"].pop("selector")
        config.write_text(tomli_w.dumps(declaration))
        original = config.read_bytes()
        cache = tmp_path / "cache"
        assert invoke_install(config, cache)[0] == 0
        assert config.read_bytes() == original
        lockfile = config.with_name("vcsinstall.lock")
        recorded = tomllib.loads(lockfile.read_text())
        assert recorded["dependencies"]["ci"]["selector"] == {"kind": "branch", "value": "trunk"}
        recorded["dependencies"]["ci"]["repository"] = "../source"
        lockfile.write_text("# retain relative native locator\n" + tomli_w.dumps(recorded))
        before = lockfile.read_bytes()
        client.run(["checkout", "-b", "new-default"]).unwrap()
        (client.path / "guide.txt").write_bytes(b"new default content")
        client.add(".").unwrap()
        client.commit("New default branch").unwrap()
        (config.parent / "z-guide.txt").unlink()

        code, records, output = invoke_install(config, cache, "--locked")

        assert code == 0, output
        assert lockfile.read_bytes() == before
        assert records[0]["repository"] == str(client.path)
        assert records[0]["ref"] == "trunk"
        assert (config.parent / "z-guide.txt").read_bytes() == b"original\n"

    @pytest.mark.parametrize("state", ["missing", "malformed", "symlink"])
    def test_invalid_project_state_preserves_shared_loading_diagnostics(self, tmp_path: Path, state: str) -> None:
        config = tmp_path / "vcsinstall.toml"
        lockfile = config.with_name("vcsinstall.lock")
        expected_code = config_errors.Unreadable.model_fields["code"].default
        if state != "missing":
            config.write_text("version = 1\n")
            if state == "malformed":
                lockfile.write_text("invalid[")
                expected_code = config_errors.InvalidToml.model_fields["code"].default
            else:
                target = tmp_path / "target"
                target.write_text("version = 1\n")
                lockfile.symlink_to(target)

        code, records, output = invoke_install(config, tmp_path / "cache")

        assert code == (3 if state == "symlink" else 2), output
        if state != "symlink":
            assert records[0]["code"] == expected_code
        assert not (tmp_path / "cache").exists()

    @pytest.mark.parametrize("locked", [False, True])
    def test_rejects_production_noop_even_when_lock_state_needs_no_source(self, tmp_path: Path, locked: bool) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text('version = 1\n[dependencies.fixture]\nvcs = "noop"\nrepository = "fixture-id"\n')
        lockfile = config.with_name("vcsinstall.lock")
        lockfile.write_text(config.read_text() + 'revision = "fixture-revision"\n')
        before = config.read_bytes(), lockfile.read_bytes()

        code, records, output = invoke_install(config, tmp_path / "cache", *(("--locked",) if locked else ()))

        assert code == 1, output
        assert records[0]["type"] == "error"
        assert "noop" in output
        assert (config.read_bytes(), lockfile.read_bytes()) == before
        assert not (tmp_path / "cache").exists()
