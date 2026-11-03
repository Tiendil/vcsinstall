import os
from pathlib import Path
from typing import BinaryIO

import pytest
import tomli_w
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.config import load_config
from llm_tool_cli.core import settings
from llm_tool_cli.core.result import Err, Result
from llm_tool_cli.paths import ProjectPathId
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from vcsinstall.cli.application import app
from vcsinstall.cli.commands.tests.helpers import (
    invoke_add,
    invoke_dependency_push,
    invoke_dependency_update,
    invoke_entry_add,
    invoke_entry_list,
)
from vcsinstall.domain import DependencyName, RepositoryLocator
from vcsinstall.filesystem import errors as filesystem_errors
from vcsinstall.hosting import GitHostingAdapter, errors
from vcsinstall.vcs_clients import GitClient, GitObjectId, clone_mirror
from vcsinstall.vcs_clients import errors as vcs_errors
from vcsinstall.vcs_clients.tests.helpers import create_repository
from vcsinstall.workspace import Config, Lockfile
from vcsinstall.workspace import errors as workspace_errors


@pytest.fixture(autouse=True)
def initialize_settings(isolated_settings: None) -> None:
    settings.initialize(
        label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
    )


@pytest.fixture
def push_project(tmp_path: Path, mocker: MockerFixture) -> tuple[Path, Path, GitClient]:
    mocker.patch.dict(
        os.environ,
        {
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "author@example.com",
            "GIT_COMMITTER_NAME": "Test Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.com",
        },
    )
    client = create_repository(tmp_path / "source")
    (client.path / "bundle").mkdir()
    (client.path / "bundle/a.txt").write_bytes(b"first\n")
    (client.path / "bundle/b.txt").write_bytes(b"second\n")
    client.add(".").unwrap()
    client.commit("Directory baseline").unwrap()
    project = tmp_path / "project"
    project.mkdir()
    config = project / "vcsinstall.toml"
    config.write_text("version = 1\n")
    cache = tmp_path / "cache"
    assert invoke_add(config, cache, "ci", str(client.path))[0] == 0
    assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/guide.txt")[0] == 0
    assert invoke_entry_add(config, cache, "ci@/bundle", "@/docs")[0] == 0
    return config, cache, client


def saved_project(config: Path) -> tuple[bytes, bytes]:
    return config.read_bytes(), config.with_name("vcsinstall.lock").read_bytes()


def assert_project_unchanged(config: Path, before: tuple[bytes, bytes]) -> None:
    assert saved_project(config) == before


def bare_recipient(source: GitClient, path: Path) -> GitClient:
    clone_mirror(RepositoryLocator(str(source.path)), path).unwrap()
    return GitClient(path=path, repository=RepositoryLocator(str(path)))


class TestPushDependency:
    @pytest.mark.parametrize("mode,extra", [("direct", []), ("pr", ["--hosting-provider", "github"])])
    def test_pins_require_explicit_destination_or_base_before_payloads(
        self, push_project: tuple[Path, Path, GitClient], mode: str, extra: list[str], mocker: MockerFixture
    ) -> None:
        config, cache, source = push_project
        source.create_tag("v1").unwrap()
        assert invoke_add(config, cache, "--redefine", "ci", "--tag", "v1", str(source.path))[0] == 0
        prepare = mocker.patch(
            "vcsinstall.cli.commands.push._prepare_message",
            side_effect=AssertionError("Invalid pin must precede message preparation"),
        )
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", mode, *extra, "-m", "message")
        assert status == 1 and records[0]["code"] == InvalidArguments.model_fields["code"].default
        prepare.assert_not_called()

    def test_native_recipient_name_preserves_literal_hash(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, source = push_project
        recipient = bare_recipient(source, tmp_path / "recipient#literal.git")
        (config.parent / "guide.txt").write_bytes(b"contribution")
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "branch", "--push-repository", str(recipient.path)
        )
        assert status == 0 and records[0]["push_repository"] == str(recipient.path)

    def test_locked_selector_remains_the_direct_default_for_omitted_declaration(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, source = push_project
        configuration = load_config(config, Config).unwrap()
        name = DependencyName("ci")
        configuration = configuration.with_dependency(name, configuration.dependencies[name].replace(selector=None))
        config.write_text(tomli_w.dumps(configuration.model_dump(mode="json", exclude_none=True)))
        recipient = bare_recipient(source, tmp_path / "recipient.git")
        recipient.create_branch("new-default").unwrap()
        recipient.set_symbolic_reference("HEAD", "refs/heads/new-default").unwrap()
        (config.parent / "guide.txt").write_bytes(b"contribution")
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "direct", "--push-repository", str(recipient.path)
        )
        assert status == 0 and records[0]["branch"] == "trunk" and records[0]["ref"] == "trunk"

    def test_linked_source_administration_and_unchanged_patch_paths_are_protected(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, source = push_project
        linked = tmp_path / "linked-source"
        source.run(["worktree", "add", "--detach", "--", str(linked)]).unwrap()
        configuration = load_config(config, Config).unwrap()
        name = DependencyName("ci")
        configuration = configuration.with_dependency(
            name, configuration.dependencies[name].replace(repository=str(linked))
        )
        config.write_text(tomli_w.dumps(configuration.model_dump(mode="json", exclude_none=True)))
        lock_path = config.with_name("vcsinstall.lock")
        locked = load_config(lock_path, Lockfile).unwrap()
        locked = locked.with_dependency(name, locked.dependencies[name].replace(repository=str(linked)))
        lock_path.write_text(tomli_w.dumps(locked.model_dump(mode="json", exclude_none=True)))
        marker = linked / ".git"
        before = marker.read_bytes()
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "patch", "-o", str(marker))
        assert status == 1 and records[0]["code"] == InvalidArguments.model_fields["code"].default
        assert marker.read_bytes() == before

    def test_direct_rejects_branch_checked_out_in_another_worktree(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, source = push_project
        recipient = bare_recipient(source, tmp_path / "recipient.git")
        recipient.run(["worktree", "add", "--", str(tmp_path / "recipient-worktree"), "trunk"]).unwrap()
        (config.parent / "guide.txt").write_text("contribution")
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "direct", "--push-repository", str(recipient.path)
        )
        assert status == 3 and "checked out" in str(records)

    def test_no_entries_or_changes_need_no_lockfile(self, tmp_path: Path) -> None:
        config = tmp_path / "vcsinstall.toml"
        config.write_text(
            'version = 1\n[dependencies.ci]\nvcs = "git"\nrepository = "/unavailable"\n'
            'selector = {kind="branch", value="main"}\n'
        )
        assert invoke_dependency_push(config, tmp_path / "cache", "ci", "--mode", "branch")[:2] == (0, [])
        assert not config.with_name("vcsinstall.lock").exists()

    def test_empty_selection_still_validates_patch_output(self, tmp_path: Path) -> None:
        config = tmp_path / "vcsinstall.toml"
        declaration = (
            'version = 1\n[dependencies.ci]\nvcs = "git"\nrepository = "/unavailable"\n'
            'selector = {kind="branch", value="main"}\n'
        )
        config.write_text(declaration)

        status, records, _ = invoke_dependency_push(
            config, tmp_path / "cache", "ci", "--mode", "patch", "--output", str(config)
        )

        assert status == 1 and records[0]["code"] == InvalidArguments.model_fields["code"].default
        assert config.read_text() == declaration
        assert not config.with_name("vcsinstall.lock").exists()

    def test_sha256_git_objects_are_supported(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, _ = push_project
        source = create_repository(tmp_path / "sha256-source", object_format="sha256")
        assert invoke_add(config, cache, "sha", str(source.path))[0] == 0
        assert invoke_entry_add(config, cache, "sha@/guide.txt", "@/sha-guide.txt")[0] == 0
        (config.parent / "sha-guide.txt").write_bytes(b"sha256 contribution")
        status, records, _ = invoke_dependency_push(config, cache, "sha", "--mode", "branch")
        assert status == 0 and len(str(records[0]["push_revision"])) == 64
        assert len(str(records[0]["revision"])) == 64

    @pytest.mark.parametrize("native_path", [b"bundle-old/\xff.bin", b"guide.txt ", b"guide.txt-other/\xfe.bin"])
    def test_publication_preserves_native_modes_filters_and_unselected_objects(
        self, push_project: tuple[Path, Path, GitClient], native_path: bytes
    ) -> None:
        config, cache, source = push_project
        (source.path / ".gitattributes").write_text("*.txt text eol=lf\n")
        (source.path / "unselected-link").symlink_to("guide.txt")
        unselected = source.path / os.fsdecode(native_path)
        unselected.parent.mkdir(parents=True, exist_ok=True)
        unselected.write_bytes(b"preserve native identity")
        source.add(".").unwrap()
        source.commit("Attributes and unselected link").unwrap()
        original = {entry.path: entry for entry in source.list_tree(source.resolve_commit("HEAD").unwrap()).unwrap()}
        assert invoke_dependency_update(config, cache, "ci")[0] == 0
        (config.parent / "guide.txt").write_bytes(b"literal\r\n")
        (config.parent / "guide.txt").chmod(0o755)
        (config.parent / "docs/new-script").write_bytes(b"#!/bin/sh\n")
        (config.parent / "docs/new-script").chmod(0o755)
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "branch", "--branch", "modes")
        assert status == 0
        revision = source.resolve_commit("refs/heads/modes").unwrap()
        assert source.run(["show", f"{revision}:guide.txt"]).unwrap() == b"literal\r\n"
        entries = {entry.path: entry for entry in source.list_tree(revision).unwrap()}
        assert entries[b"guide.txt"].mode == b"100644"
        assert entries[b"bundle/new-script"].mode == b"100755"
        assert entries[b"unselected-link"].mode == b"120000"
        assert entries[native_path] == original[native_path]
        assert len(records) == 2

    def test_permission_only_change_does_not_publish(self, push_project: tuple[Path, Path, GitClient]) -> None:
        config, cache, _ = push_project
        (config.parent / "guide.txt").chmod(0o755)
        assert invoke_dependency_push(config, cache, "ci", "--mode", "branch")[1] == []

    def test_branch_preserves_recipient_worktree_index_and_configuration(
        self, push_project: tuple[Path, Path, GitClient]
    ) -> None:
        config, cache, source = push_project
        (source.path / "guide.txt").write_text("staged source edit")
        source.add("guide.txt").unwrap()
        (source.path / "bundle/a.txt").write_text("unstaged source edit")
        before = (
            (source.path / ".git/index").read_bytes(),
            (source.path / ".git/config").read_bytes(),
            source.run(["symbolic-ref", "HEAD"]).unwrap(),
        )
        (config.parent / "guide.txt").write_text("independent contribution")
        status, _, _ = invoke_dependency_push(config, cache, "ci", "--mode", "branch")
        assert status == 0
        assert (source.path / ".git/index").read_bytes() == before[0]
        assert (source.path / ".git/config").read_bytes() == before[1]
        assert source.run(["symbolic-ref", "HEAD"]).unwrap() == before[2]
        assert (source.path / "guide.txt").read_text() == "staged source edit"
        assert (source.path / "bundle/a.txt").read_text() == "unstaged source edit"

    def test_receiving_policy_rejection_is_preserved(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, source = push_project
        recipient = bare_recipient(source, tmp_path / "recipient.git")
        hook = recipient.path / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho 'receiving policy rejects contribution' >&2\nexit 1\n")
        hook.chmod(0o755)
        (config.parent / "guide.txt").write_text("changed")
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "branch", "--branch", "policy", "--push-repository", str(recipient.path)
        )
        assert status == 3 and "receiving policy rejects contribution" in str(records)
        assert not recipient.reference_exists("refs/heads/policy").unwrap()

    def test_target_project_repository_cannot_be_publication_recipient(
        self, push_project: tuple[Path, Path, GitClient]
    ) -> None:
        config, cache, _ = push_project
        target = create_repository(config.parent)
        before = target.resolve_commit("HEAD").unwrap()
        (config.parent / "guide.txt").write_text("changed")
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "branch", "--push-repository", str(config.parent)
        )
        assert status == 3 and "target project's own Git repository" in str(records)
        assert target.resolve_commit("HEAD").unwrap() == before

    @pytest.mark.parametrize("compatible", [True, False])
    def test_duplicate_source_mappings_require_compatible_complete_content(
        self, push_project: tuple[Path, Path, GitClient], compatible: bool
    ) -> None:
        config, cache, _ = push_project
        assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/second-guide.txt")[0] == 0
        (config.parent / "guide.txt").write_text("shared edit")
        (config.parent / "second-guide.txt").write_text("shared edit" if compatible else "conflicting edit")
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "branch")
        if compatible:
            assert status == 0 and len(records) == 2
            assert records[0]["push_revision"] == records[1]["push_revision"]
        else:
            assert status == 1 and records[0]["code"] == InvalidArguments.model_fields["code"].default

    def test_unselected_local_unsupported_content_is_ignored(self, push_project: tuple[Path, Path, GitClient]) -> None:
        config, cache, _ = push_project
        (config.parent / "docs/link").symlink_to("a.txt")
        (config.parent / "guide.txt").write_text("changed")
        assert invoke_dependency_push(config, cache, "ci", "@/guide.txt", "--mode", "branch")[0] == 0
        assert invoke_dependency_push(config, cache, "ci", "--mode", "branch")[0] == 3

    def test_branch_uses_locked_parent_complete_tree_and_preserves_project(
        self, push_project: tuple[Path, Path, GitClient]
    ) -> None:
        config, cache, source = push_project
        root = config.parent
        baseline = source.resolve_commit("HEAD").unwrap()
        before = saved_project(config)
        (root / "docs/a.txt").write_bytes(b"edited\r\n")
        (root / "docs/b.txt").unlink()
        (root / "docs/.new.bin").write_bytes(b"\x00new\xffbinary")
        (root / "docs/empty").mkdir()
        (source.path / "unrelated").write_text("upstream\n")
        source.add(".").unwrap()
        upstream = source.commit("Upstream advances").unwrap()

        status, records, _ = invoke_dependency_push(
            config,
            cache,
            "ci",
            "--mode",
            "branch",
            "--branch",
            "contribute/docs",
            "-m",
            "Update guides",
            "-m",
            "Include additions and deletions",
        )

        assert status == 0
        assert len(records) == 1
        record = records[0]
        assert record["operation"] == "dependency push"
        assert record["destination"] == "@/docs"
        assert record["outcome"] == "pushed" and record["status"] == "modified"
        assert record["revision"] == baseline and record["push_repository"] == str(source.path)
        assert record["hosting_provider"] == "git" and record["content"] is None
        revision = source.resolve_commit("refs/heads/contribute/docs").unwrap()
        commit = source.inspect_commit(revision).unwrap()
        assert commit.parents == (baseline,)
        assert commit.message == b"Update guides\n\nInclude additions and deletions\n"
        assert source.run(["show", f"{revision}:bundle/a.txt"]).unwrap() == b"edited\r\n"
        assert source.run(["show", f"{revision}:bundle/.new.bin"]).unwrap() == b"\x00new\xffbinary"
        assert source.run(["cat-file", "-e", f"{revision}:bundle/b.txt"]).is_err()
        assert source.run(["cat-file", "-e", f"{revision}:unrelated"]).is_err()
        assert source.resolve_commit("refs/heads/trunk").unwrap() == upstream
        assert_project_unchanged(config, before)
        assert invoke_entry_list(config, "@/docs")[1][0]["status"] == "modified"

    @pytest.mark.parametrize("mode", ["branch", "direct", "patch", "pr"])
    def test_unchanged_selection_needs_no_source_or_delivery(
        self, push_project: tuple[Path, Path, GitClient], mode: str, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        config, cache, source = push_project
        source.path.rename(tmp_path / "unavailable-source")
        output = tmp_path / "patch"
        output.write_bytes(b"keep")
        arguments = ["ci", "@/guide.txt", "--mode", mode]
        if mode == "patch":
            arguments += ["--output", str(output)]
        if mode == "pr":
            arguments += ["--hosting-provider", "github"]
        mocker.patch.object(
            GitHostingAdapter, "acquire", side_effect=AssertionError("unchanged selection must be local")
        )
        status, records, _ = invoke_dependency_push(config, cache, *arguments)
        assert status == 0 and len(records) == 1
        assert records[0]["outcome"] == "unchanged"
        assert all(
            records[0][field] is None
            for field in (
                "push_repository",
                "branch",
                "push_revision",
                "hosting_provider",
                "review_url",
                "base_branch",
                "patch_file",
            )
        )
        assert output.read_bytes() == b"keep"
        assert invoke_dependency_push(config, cache, *(["ci", "--mode", mode] + arguments[4:]))[1] == []

    def test_automatic_branch_and_explicit_unchanged_record(self, push_project: tuple[Path, Path, GitClient]) -> None:
        config, cache, _ = push_project
        (config.parent / "guide.txt").write_bytes(b"edit")
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "@/guide.txt", "@/docs", "@/guide.txt", "--mode", "branch"
        )
        assert status == 0
        assert [record["destination"] for record in records] == ["@/docs", "@/guide.txt"]
        assert records[0]["outcome"] == "unchanged" and records[0]["branch"] is None
        assert records[1]["branch"] and records[1]["outcome"] == "pushed"

    def test_branch_collision_preserves_existing_branch(self, push_project: tuple[Path, Path, GitClient]) -> None:
        config, cache, source = push_project
        source.create_branch("contribution").unwrap()
        revision = source.resolve_commit("refs/heads/contribution").unwrap()
        (config.parent / "guide.txt").write_text("edited")
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "branch", "--branch", "contribution"
        )
        assert status == 3 and records[0]["code"] == errors.BranchExists.model_fields["code"].default
        assert source.resolve_commit("refs/heads/contribution").unwrap() == revision

    def test_patch_is_binary_mailbox_applicable_with_git_am(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, source = push_project
        before = saved_project(config)
        baseline = source.resolve_commit("HEAD").unwrap()
        (config.parent / "docs/a.txt").write_bytes(b"\0binary\xff")
        (config.parent / "docs/new.txt").write_text("new")
        patch = tmp_path / "changes.patch"
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "patch", "-o", str(patch), "-m", "Binary contribution"
        )
        assert status == 0 and records[0]["outcome"] == "exported"
        assert records[0]["patch_file"] == str(patch) and records[0]["push_revision"] is None
        assert b"base-commit: " + baseline.encode("ascii") in patch.read_bytes()
        applied = tmp_path / "applied"
        source.run(["clone", "--no-hardlinks", "--", str(source.path), str(applied)]).unwrap()
        recipient = GitClient(path=applied, repository=RepositoryLocator(str(applied)))
        recipient.run(["am", "--", str(patch)]).unwrap()
        assert (applied / "bundle/a.txt").read_bytes() == b"\0binary\xff"
        assert (applied / "bundle/new.txt").read_text() == "new"
        assert source.resolve_commit("HEAD").unwrap() == baseline
        assert_project_unchanged(config, before)

    def test_patch_export_uses_resolved_destination(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, _ = push_project
        directory = tmp_path / "exports"
        directory.mkdir()
        alias = tmp_path / "exports-alias"
        alias.symlink_to(directory, target_is_directory=True)
        (config.parent / "guide.txt").write_text("changed")

        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "patch", "--output", str(alias / "fix.patch")
        )

        assert status == 0 and records[0]["patch_file"] == str(directory / "fix.patch")
        assert (directory / "fix.patch").is_file()

    @pytest.mark.parametrize("existing", [False, True])
    def test_failed_patch_export_reports_failure_and_leaves_partial_output(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path, mocker: MockerFixture, existing: bool
    ) -> None:
        config, cache, _ = push_project
        before = saved_project(config)
        (config.parent / "guide.txt").write_text("changed")
        patch = tmp_path / "output.patch"
        if existing:
            patch.write_bytes(b"previous output")

        def fail_format_patch(revision: GitObjectId, base: GitObjectId, output: BinaryIO) -> Result[None]:
            output.write(b"partial")
            return Err([filesystem_errors.AccessFailed(path=patch, reason="injected failure")])

        mocker.patch.object(GitClient, "format_patch", side_effect=fail_format_patch)
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "patch", "-o", str(patch))

        assert status == 3 and records[0]["code"] == filesystem_errors.AccessFailed.model_fields["code"].default
        assert patch.read_bytes() == b"partial"
        assert_project_unchanged(config, before)

    @pytest.mark.parametrize(
        "target",
        ["config", "lock", "destination", "destination-child", "cache", "administration", "directory", "symlink"],
    )
    def test_patch_rejects_protected_or_nonregular_target(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path, target: str
    ) -> None:
        config, cache, source = push_project
        (config.parent / "guide.txt").write_text("changed")
        link = tmp_path / "link"
        link.symlink_to(config)
        targets = {
            "config": config,
            "lock": config.with_name("vcsinstall.lock"),
            "destination": config.parent / "guide.txt",
            "destination-child": config.parent / "docs/output.patch",
            "cache": cache / "output.patch",
            "administration": source.path / ".git/output.patch",
            "directory": tmp_path,
            "symlink": link,
        }
        before = saved_project(config)
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "patch", "-o", str(targets[target]))
        assert status != 0 and all(record["type"] == "error" for record in records)
        assert_project_unchanged(config, before)

    def test_direct_uses_current_recipient_and_is_idempotent(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path
    ) -> None:
        config, cache, source = push_project
        baseline = source.resolve_commit("HEAD").unwrap()
        before = saved_project(config)
        (config.parent / "guide.txt").write_text("local contribution")
        (source.path / "unrelated.txt").write_text("new upstream")
        source.add(".").unwrap()
        upstream = source.commit("Unrelated advance").unwrap()
        recipient = bare_recipient(source, tmp_path / "recipient.git")
        source.path.rename(tmp_path / "unavailable-source")
        args = ["ci", "--mode", "direct", "--push-repository", str(recipient.path)]
        status, records, _ = invoke_dependency_push(config, cache, *args)
        assert status == 0 and records[0]["revision"] == baseline
        revision = recipient.resolve_commit("refs/heads/trunk").unwrap()
        assert recipient.inspect_commit(revision).unwrap().parents == (upstream,)
        assert recipient.run(["show", f"{revision}:guide.txt"]).unwrap() == b"local contribution"
        assert recipient.run(["show", f"{revision}:unrelated.txt"]).unwrap() == b"new upstream"
        status, retried, _ = invoke_dependency_push(config, cache, *args)
        assert status == 0 and retried[0]["outcome"] == "pushed"
        assert retried[0]["push_revision"] == revision
        assert_project_unchanged(config, before)

    def test_direct_directory_conflict_prevents_all_writes(
        self, push_project: tuple[Path, Path, GitClient], tmp_path: Path, mocker: MockerFixture
    ) -> None:
        config, cache, source = push_project
        (config.parent / "guide.txt").write_text("first local change")
        (config.parent / "docs/a.txt").write_text("second local change")
        (source.path / "bundle/remote-only").write_text("upstream addition")
        source.add(".").unwrap()
        revision = source.commit("Source-only directory addition").unwrap()
        recipient = bare_recipient(source, tmp_path / "recipient.git")
        commit = mocker.spy(GitClient, "commit_tree")
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "direct", "--push-repository", str(recipient.path)
        )
        assert status == 3 and records[0]["code"] == errors.DestinationConflict.model_fields["code"].default
        assert records[0]["source_path"] == "@/bundle"
        assert recipient.resolve_commit("refs/heads/trunk").unwrap() == revision
        commit.assert_not_called()

    def test_direct_rejects_checked_out_recipient(self, push_project: tuple[Path, Path, GitClient]) -> None:
        config, cache, source = push_project
        revision = source.resolve_commit("HEAD").unwrap()
        (config.parent / "guide.txt").write_text("changed")
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "direct")
        assert status == 3 and "checked out" in str(records)
        assert source.resolve_commit("HEAD").unwrap() == revision

    @pytest.mark.parametrize(
        "arguments",
        [
            ["--mode", "patch"],
            ["--mode", "patch", "-o", "-"],
            ["--mode", "patch", "-o", "@/out"],
            ["--mode", "branch", "--base", "trunk"],
            ["--mode", "branch", "-o", "output"],
            ["--mode", "branch", "--hosting-provider", "filesystem"],
            ["--mode", "overwrite"],
            ["--mode", "pr", "--hosting-provider", "git"],
            ["--mode", "branch", "--branch", "bad..branch"],
            ["--mode", "overwrite", "-m", "text"],
        ],
    )
    def test_invalid_options_precede_message_preparation_and_source_access(
        self, push_project: tuple[Path, Path, GitClient], arguments: list[str], mocker: MockerFixture
    ) -> None:
        config, cache, _ = push_project
        acquire = mocker.patch.object(
            GitHostingAdapter, "acquire", side_effect=AssertionError("invalid arguments must not acquire")
        )
        prepare = mocker.patch(
            "vcsinstall.cli.commands.push._prepare_message",
            side_effect=AssertionError("Invalid options must precede message preparation"),
        )
        status, records, _ = invoke_dependency_push(config, cache, "ci", *arguments, "-m", "message")
        assert status == 1 and records[0]["code"] == InvalidArguments.model_fields["code"].default
        acquire.assert_not_called()
        prepare.assert_not_called()

    @pytest.mark.parametrize("selection", ["unknown", "child", "different-dependency"])
    def test_invalid_selection_precedes_acquisition(
        self, push_project: tuple[Path, Path, GitClient], selection: str, mocker: MockerFixture
    ) -> None:
        config, cache, source = push_project
        name, path = "ci", "@/docs/a.txt" if selection == "child" else "@/not-declared"
        if selection == "unknown":
            name, path = "absent", "@/guide.txt"
        if selection == "different-dependency":
            assert invoke_add(config, cache, "other", str(source.path))[0] == 0
            assert invoke_entry_add(config, cache, "other@/guide.txt", "@/other.txt")[0] == 0
            path = "@/other.txt"
        acquire = mocker.patch.object(GitHostingAdapter, "acquire", side_effect=AssertionError("invalid selection"))
        status, records, _ = invoke_dependency_push(config, cache, name, path, "--mode", "branch")
        assert status == 1 and records[0]["code"] == InvalidArguments.model_fields["code"].default
        acquire.assert_not_called()

    def test_missing_baseline_precedes_acquisition_and_unselected_entries_are_not_verified(
        self, push_project: tuple[Path, Path, GitClient], mocker: MockerFixture
    ) -> None:
        config, cache, _ = push_project
        locked_path = config.with_name("vcsinstall.lock")
        locked = load_config(locked_path, Lockfile).unwrap()
        locked_path.write_text(
            tomli_w.dumps(
                locked.without_entries((ProjectPathId("@/docs"),)).model_dump(mode="json", exclude_none=True)
            )
        )
        acquire = mocker.patch.object(GitHostingAdapter, "acquire", side_effect=AssertionError("unavailable baseline"))
        assert invoke_dependency_push(config, cache, "ci", "@/guide.txt", "--mode", "branch")[0] == 0
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "branch")
        assert status == 3 and records[0]["code"] == workspace_errors.MissingBaseline.model_fields["code"].default
        acquire.assert_not_called()

    def test_multiline_message_text(self, push_project: tuple[Path, Path, GitClient]) -> None:
        config, cache, source = push_project
        (config.parent / "guide.txt").write_text("changed")
        message = "Subject\n\nBody with literal + and spaces.\n"
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "branch", "--message", message)
        assert status == 0
        revision = source.resolve_commit(f"refs/heads/{records[0]['branch']}").unwrap()
        assert source.inspect_commit(revision).unwrap().message == message.encode("utf-8")

    def test_nul_message_failure_is_reported_by_git(self, push_project: tuple[Path, Path, GitClient]) -> None:
        config, cache, source = push_project
        (config.parent / "guide.txt").write_text("changed")
        message = "Subject\x00body"
        before = saved_project(config)

        status, records, _ = invoke_dependency_push(
            config,
            cache,
            "ci",
            "--mode",
            "branch",
            "--branch",
            "nul-message",
            "--message",
            message,
        )

        assert status == 3 and records[0]["code"] == vcs_errors.CommandFailed.model_fields["code"].default
        assert "NUL" in str(records[0]["reason"])
        assert not source.reference_exists("refs/heads/nul-message").unwrap()
        assert_project_unchanged(config, before)

    def test_file_deletion_and_new_directory_file_remain_local_changes(
        self, push_project: tuple[Path, Path, GitClient]
    ) -> None:
        config, cache, source = push_project
        (config.parent / "guide.txt").unlink()
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "branch", "--branch", "delete-guide"
        )
        assert status == 0 and records[0]["status"] == "missing"
        revision = source.resolve_commit("refs/heads/delete-guide").unwrap()
        assert source.run(["cat-file", "-e", f"{revision}:guide.txt"]).is_err()
        assert not (config.parent / "guide.txt").exists()

    def test_no_root_push_and_help_are_project_independent(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(app, ["--config", str(tmp_path / "missing"), "dependency", "push", "--help"])
        assert result.exit_code == 0 and "--mode" in result.stdout
        assert "--file" not in result.stdout and "-F" not in result.stdout
        assert CliRunner().invoke(app, ["push"]).exit_code != 0

    @pytest.mark.parametrize("option,value", [("--file", "message.txt"), ("-F", "-")])
    def test_message_file_options_are_rejected(self, tmp_path: Path, option: str, value: str) -> None:
        result = CliRunner().invoke(
            app, ["--config", str(tmp_path / "missing"), "dependency", "push", "ci", "--mode", "branch", option, value]
        )

        assert result.exit_code == 2


class TestFilesystemPush:
    @pytest.mark.parametrize("with_files", [False, True])
    def test_overwrite_preserves_source_root_when_replacing_all_its_content(
        self, tmp_path: Path, with_files: bool
    ) -> None:
        source = tmp_path / "source"
        (source / "nested").mkdir(parents=True)
        (source / "nested/old").write_bytes(b"old")
        project = tmp_path / "project"
        project.mkdir()
        config = project / "vcsinstall.toml"
        config.write_text("version = 1\n")
        cache = tmp_path / "unused-cache"
        assert invoke_add(config, cache, "local", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "local@/", "@/installed")[0] == 0
        before = saved_project(config)
        installed = project / "installed"
        (installed / "nested/old").unlink()
        (installed / "nested").rmdir()
        if with_files:
            (installed / "new").write_bytes(b"new")
        else:
            installed.rmdir()
        original = source.stat()

        status, records, _ = invoke_dependency_push(config, cache, "local", "--mode", "overwrite")

        assert status == 0 and records[0]["outcome"] == "pushed"
        assert source.is_dir() and source.stat().st_ino == original.st_ino
        assert source.stat().st_mode == original.st_mode
        assert sorted(path.name for path in source.iterdir()) == (["new"] if with_files else [])
        if with_files:
            assert (source / "new").read_bytes() == b"new"
        assert_project_unchanged(config, before)

    @pytest.mark.parametrize("replacement", ["directory", "file", "absent"])
    def test_overwrite_reproduces_complete_tree_without_advancing_baseline(  # noqa: CCR001 — Cover root kinds.
        self, tmp_path: Path, replacement: str
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "selected").mkdir()
        (source / "selected/old").write_text("old")
        (source / "unrelated").write_text("preserve")
        project = tmp_path / "project"
        project.mkdir()
        config, cache = project / "vcsinstall.toml", tmp_path / "cache-is-a-file"
        config.write_text("version = 1\n")
        cache.write_text("unusable cache is irrelevant")
        assert invoke_add(config, cache, "local", source.as_uri())[0] == 0
        assert invoke_entry_add(config, cache, "local@/selected", "@/installed")[0] == 0
        before = saved_project(config)
        (source / "selected/remote-only").write_text("source changed")
        (project / "installed/old").unlink()
        if replacement == "directory":
            (project / "installed/.new").write_bytes(b"\0exact\xff")
        else:
            (project / "installed").rmdir()
            if replacement == "file":
                (project / "installed").write_text("file replaces directory")
        status, records, _ = invoke_dependency_push(config, cache, "local", "--mode", "overwrite")
        assert status == 0 and records[0]["outcome"] == "pushed"
        assert records[0]["hosting_provider"] == "filesystem" and records[0]["push_revision"] != records[0]["revision"]
        assert records[0]["push_repository"] == str(source)
        assert (source / "unrelated").read_text() == "preserve"
        if replacement == "directory":
            assert (source / "selected/.new").read_bytes() == b"\0exact\xff"
            assert not (source / "selected/remote-only").exists()
        elif replacement == "file":
            assert (source / "selected").read_text() == "file replaces directory"
        else:
            assert not (source / "selected").exists()
        assert_project_unchanged(config, before)
        assert invoke_dependency_update(config, cache, "local")[0] == 0

    @pytest.mark.parametrize(
        "arguments",
        [
            ["--mode", "branch"],
            ["--mode", "overwrite", "-m", "text"],
            ["--mode", "overwrite", "--push-repository", "somewhere"],
        ],
    )
    def test_unsupported_modes_and_options_are_invalid(self, tmp_path: Path, arguments: list[str]) -> None:
        source = tmp_path / "source"
        source.mkdir()
        config = tmp_path / "vcsinstall.toml"
        config.write_text(f'version = 1\n[dependencies.local]\nvcs = "filesystem"\nrepository = "{source}"\n')
        status, records, _ = invoke_dependency_push(config, tmp_path / "cache", "local", *arguments)
        assert status == 1 and records[0]["code"] == InvalidArguments.model_fields["code"].default
