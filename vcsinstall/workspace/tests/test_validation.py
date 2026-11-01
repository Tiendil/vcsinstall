from pathlib import Path

import pytest
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.paths import ProjectConfigPath, ProjectPathId
from pytest_mock import MockerFixture

from vcsinstall.domain import DependencyName, SourceRevision, VcsId
from vcsinstall.domain.tests import make
from vcsinstall.sources import get_adapter
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
from vcsinstall.workspace.validation import validate_workspace_destinations


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


class TestRequireMatchingDependencyLock:
    @pytest.mark.parametrize("vcs", list(VcsId))
    @pytest.mark.parametrize(
        "state", ["matching", "missing_file", "missing_name", "selector", "repository", "backend"]
    )
    def test_requires_matching_name_repository_and_selector(  # noqa: CCR001 — Exercise independent lock mismatches.
        self, workspace: Workspace, vcs: VcsId, state: str
    ) -> None:
        name = DependencyName("ci")
        declared = make.dependency(vcs=vcs.value, selector=make.selector("branch", "trunk"))
        recorded = DependencyLock(**declared.model_dump(), revision=SourceRevision("recorded-revision"))
        if state == "selector":
            recorded = recorded.replace(selector=make.selector("branch", "other"))
        elif state == "repository":
            recorded = recorded.replace(repository=make.dependency(repository="other").repository)
        elif state == "backend":
            recorded = recorded.replace(vcs=VcsId.noop if vcs != VcsId.noop else VcsId.git)
        workspace = workspace.replace(
            config=Config(version=1, dependencies={name: declared}),
            lockfile=(
                None
                if state == "missing_file"
                else Lockfile(version=1, dependencies={} if state == "missing_name" else {name: recorded})
            ),
        )

        result = workspace.require_matching_dependency_lock(name, source=get_adapter(vcs))

        assert result.is_ok() == (state == "matching")
        if result.is_err():
            problem = result.unwrap_err()[0]
            assert isinstance(problem, errors.DependencyLockRequired)
            assert problem.path == workspace.lockfile_path
            assert problem.dependency == name

    @pytest.mark.parametrize("kind,matches", [("branch", True), ("tag", False), ("revision", False)])
    def test_omitted_git_selector_only_reuses_a_recorded_branch(
        self, workspace: Workspace, kind: str, matches: bool
    ) -> None:
        name = DependencyName("ci")
        declared = make.dependency()
        workspace = workspace.replace(
            config=Config(version=1, dependencies={name: declared}),
            lockfile=Lockfile(
                version=1,
                dependencies={
                    name: DependencyLock(
                        **declared.replace(selector=make.selector(kind, "selected")).model_dump(),
                        revision=SourceRevision("recorded"),
                    )
                },
            ),
        )

        result = workspace.require_matching_dependency_lock(name, source=get_adapter(VcsId.git))

        assert result.is_ok() == matches
        if matches:
            assert result.unwrap().selector == make.selector("branch", "selected")

    def test_unknown_name_is_an_invalid_argument(self, workspace: Workspace) -> None:
        result = workspace.require_matching_dependency_lock(DependencyName("missing"), source=get_adapter(VcsId.noop))

        assert result.is_err(InvalidArguments)

    def test_returns_the_recorded_lock_without_io_or_model_changes(
        self, workspace: Workspace, mocker: MockerFixture
    ) -> None:
        source = get_adapter(VcsId.noop)
        normalize = mocker.patch.object(source, "normalize_repository", side_effect=AssertionError("unexpected I/O"))
        resolve = mocker.patch.object(Path, "resolve", side_effect=AssertionError("unexpected filesystem access"))
        assert workspace.lockfile is not None
        recorded = workspace.lockfile.dependencies[DependencyName("ci")]
        before = workspace.model_dump()

        result = workspace.require_matching_dependency_lock(DependencyName("ci"), source=source).unwrap()

        assert result is recorded
        assert workspace.model_dump() == before
        normalize.assert_not_called()
        resolve.assert_not_called()


class TestValidateWorkspaceDestinations:
    @pytest.mark.parametrize("collection", ["config", "lockfile"])
    @pytest.mark.parametrize(
        "destination,valid",
        [
            ("@/new", True),
            ("@/installed", False),
            ("@/installed/child", False),
            ("@/vcsinstall.lock", False),
            ("@/", False),
        ],
    )
    def test_validates_each_record_collection(
        self, workspace: Workspace, collection: str, destination: str, valid: bool
    ) -> None:
        if collection == "config":
            added = workspace.config.entries[0].replace(destination=ProjectPathId(destination))
            workspace = workspace.replace(config=workspace.config.replace(entries=(*workspace.config.entries, added)))
        else:
            assert workspace.lockfile is not None
            added_baseline = workspace.lockfile.entries[0].replace(destination=ProjectPathId(destination))
            workspace = workspace.replace(
                lockfile=workspace.lockfile.replace(entries=(*workspace.lockfile.entries, added_baseline))
            )

        assert validate_workspace_destinations(workspace).is_ok() == valid

    def test_allows_missing_lock_state(self, workspace: Workspace) -> None:
        assert validate_workspace_destinations(workspace.replace(lockfile=None)).is_ok()


class TestValidateDestinationOwnership:
    def test_unrelated_stale_mapping_does_not_block_a_disjoint_destination(self, workspace: Workspace) -> None:
        previous_declaration = workspace.config.entries[0]
        workspace = workspace.replace(
            config=workspace.config.replace(
                entries=(previous_declaration.replace(destination=ProjectPathId("@/installed/child")),)
            )
        )

        assert workspace.validate_destination_ownership(ProjectPathId("@/new")).is_ok()

    @pytest.mark.parametrize("destination", ["@/new", "@/installed", "@/other"])
    def test_allows_unowned_and_exact_destinations(self, workspace: Workspace, destination: str) -> None:
        assert workspace.validate_destination_ownership(ProjectPathId(destination)).is_ok()

    @pytest.mark.parametrize("destination", ["@/installed/child", "@/other/child", "@/vcsinstall.lock", "@/"])
    def test_rejects_overlaps_and_reserved_paths(self, workspace: Workspace, destination: str) -> None:
        assert workspace.validate_destination_ownership(ProjectPathId(destination)).is_err(InvalidArguments)

    def test_uses_the_selected_config_filename(self, workspace: Workspace) -> None:
        workspace = workspace.replace(config_path=ProjectConfigPath(workspace.project_root / "custom.toml"))

        assert workspace.validate_destination_ownership(ProjectPathId("@/custom.toml")).is_err(InvalidArguments)
        assert workspace.validate_destination_ownership(ProjectPathId("@/vcsinstall.toml")).is_ok()

    def test_guards_do_not_access_files_or_modify_models(self, workspace: Workspace, mocker: MockerFixture) -> None:
        resolve = mocker.patch.object(Path, "resolve", side_effect=AssertionError("unexpected filesystem access"))
        stat = mocker.patch.object(Path, "lstat", side_effect=AssertionError("unexpected filesystem access"))
        before = workspace.model_dump()

        validate_workspace_destinations(workspace).unwrap()
        workspace.validate_destination_ownership(ProjectPathId("@/new")).unwrap()
        workspace.require_entry_baseline(workspace.config.entries[0]).unwrap()

        assert workspace.model_dump() == before
        resolve.assert_not_called()
        stat.assert_not_called()


class TestRequireEntryBaseline:
    @pytest.mark.parametrize("state", ["missing_file", "missing_record"])
    def test_reports_the_missing_record(self, workspace: Workspace, state: str) -> None:
        workspace = workspace.replace(lockfile=None if state == "missing_file" else Lockfile(version=1))

        problem = workspace.require_entry_baseline(workspace.config.entries[0]).unwrap_err()[0]

        assert isinstance(problem, errors.MissingBaseline)
        assert problem.path == workspace.lockfile_path
        assert problem.dependency == "ci"
        assert problem.destination == "@/installed"

    def test_returns_the_old_mapping_baseline_unchanged(self, workspace: Workspace) -> None:
        declaration = workspace.config.entries[0].replace(dependency=DependencyName("formatting"))
        assert workspace.lockfile is not None
        previous = workspace.lockfile.entries[0]

        assert workspace.require_entry_baseline(declaration).unwrap() is previous
