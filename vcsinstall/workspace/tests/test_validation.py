from pathlib import Path

import pytest
from llm_tool_cli.paths import ProjectConfigPath, ProjectPathId

from vcsinstall.domain import DependencyName, SourceRevision
from vcsinstall.domain.tests import make
from vcsinstall.workspace import (
    AbsentBaseline,
    Config,
    DependencyLock,
    Entry,
    Lockfile,
    LockfilePath,
    Workspace,
    errors,
    validate_destinations,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    dependencies = {DependencyName(name): make.dependency(vcs="noop") for name in ("ci", "formatting")}
    mapping = {"dependency": "ci", "source_path": "@/guide", "destination": "@/installed"}
    return Workspace(
        config_path=ProjectConfigPath(tmp_path / "vcsinstall.toml"),
        lockfile_path=LockfilePath(tmp_path / "vcsinstall.lock"),
        config=Config(version=1, dependencies=dependencies, entries=(Entry.model_validate(mapping),)),
        lockfile=Lockfile(
            version=1,
            dependencies={
                name: DependencyLock(**definition.model_dump(), revision=SourceRevision("recorded-revision"))
                for name, definition in dependencies.items()
            },
            entries=(
                AbsentBaseline.model_validate(mapping),
                AbsentBaseline.model_validate(
                    {"dependency": "formatting", "source_path": "@/other", "destination": "@/other"}
                ),
            ),
        ),
    )


class TestValidateDestinations:
    @pytest.mark.parametrize(
        "paths,valid",
        [
            ([], True),
            (["@/a", "@/ab"], True),
            (["@/a", "@/a"], False),
            (["@/a", "@/a/child"], False),
            (["@/a/child", "@/a"], False),
            (["@/"], False),
            (["@/vcsinstall.lock"], False),
            (["@/vcsinstall.toml/child"], False),
        ],
    )
    def test_entry_identity_and_reserved_paths(self, paths: list[str], valid: bool) -> None:
        result = validate_destinations(
            tuple(ProjectPathId(path) for path in paths),
            reserved=(ProjectPathId("@/vcsinstall.lock"), ProjectPathId("@/vcsinstall.toml")),
        )
        assert result.is_ok() == valid


class TestValidateNoOrphanedBaselines:
    @pytest.mark.parametrize("dependency", ["ci", "missing"])
    def test_accepts_matching_destinations_and_ignores_other_dependencies(
        self, workspace: Workspace, dependency: str
    ) -> None:
        assert workspace.validate_no_orphaned_baselines(DependencyName(dependency)).is_ok()

    @pytest.mark.parametrize("change", ["removed", "destination", "dependency"])
    def test_reports_recorded_destination_without_its_declaration(self, workspace: Workspace, change: str) -> None:
        entries: tuple[Entry, ...] = ()
        if change != "removed":
            entry = workspace.config.entries[0].model_dump()
            entry[change] = "@/moved" if change == "destination" else "formatting"
            entries = (Entry.model_validate(entry),)
        workspace = workspace.replace(config=workspace.config.replace(entries=entries))

        problem = workspace.validate_no_orphaned_baselines(DependencyName("ci")).unwrap_err()[0]

        assert isinstance(problem, errors.OrphanedBaseline)
        assert problem.path == workspace.lockfile_path
        assert problem.dependency == "ci"
        assert problem.destination == "@/installed"

    @pytest.mark.parametrize("lockfile", [None, Lockfile(version=1)])
    def test_accepts_missing_recorded_state(self, workspace: Workspace, lockfile: Lockfile | None) -> None:
        workspace = workspace.replace(lockfile=lockfile)

        assert workspace.validate_no_orphaned_baselines(DependencyName("ci")).is_ok()

    def test_does_not_require_a_baseline_for_new_declarations(self, workspace: Workspace) -> None:
        entry = Entry.model_validate({"dependency": "ci", "source_path": "@/new", "destination": "@/new"})
        workspace = workspace.replace(config=workspace.config.replace(entries=(*workspace.config.entries, entry)))

        assert workspace.validate_no_orphaned_baselines(DependencyName("ci")).is_ok()


class TestValidateNoMissingBaselines:
    def test_accepts_matching_baselines_and_ignores_other_dependencies(self, workspace: Workspace) -> None:
        entry = Entry.model_validate(
            {"dependency": "formatting", "source_path": "@/unrecorded", "destination": "@/unrecorded"}
        )
        workspace = workspace.replace(config=workspace.config.replace(entries=(*workspace.config.entries, entry)))

        assert workspace.validate_no_missing_baselines(DependencyName("ci")).is_ok()

    @pytest.mark.parametrize("state", ["missing_file", "empty_file", "missing_entry", "wrong_dependency"])
    def test_reports_missing_baselines(self, workspace: Workspace, state: str) -> None:
        recorded = workspace.lockfile
        assert recorded is not None
        lockfile: Lockfile | None
        if state == "missing_file":
            lockfile = None
        elif state == "empty_file":
            lockfile = Lockfile(version=1)
        elif state == "missing_entry":
            lockfile = recorded.replace(entries=())
        else:
            baseline = recorded.entries[0].replace(dependency=DependencyName("formatting"))
            lockfile = recorded.replace(entries=(baseline, *recorded.entries[1:]))
        workspace = workspace.replace(lockfile=lockfile)

        problem = workspace.validate_no_missing_baselines(DependencyName("ci")).unwrap_err()[0]

        assert isinstance(problem, errors.MissingBaseline)
        assert problem.path == workspace.lockfile_path
        assert problem.dependency == "ci"
        assert problem.destination == "@/installed"

    @pytest.mark.parametrize("lockfile", [None, Lockfile(version=1)])
    def test_no_declared_entries_need_no_baselines(self, workspace: Workspace, lockfile: Lockfile | None) -> None:
        workspace = workspace.replace(config=workspace.config.replace(entries=()), lockfile=lockfile)

        assert workspace.validate_no_missing_baselines(DependencyName("ci")).is_ok()

    def test_prior_source_mapping_still_supplies_a_baseline(self, workspace: Workspace) -> None:
        entry = Entry.model_validate(
            {"dependency": "ci", "source_path": "@/new-source-path", "destination": "@/installed"}
        )
        workspace = workspace.replace(config=workspace.config.replace(entries=(entry,)))

        assert workspace.validate_no_missing_baselines(DependencyName("ci")).is_ok()
