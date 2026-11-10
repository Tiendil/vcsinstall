import json
import os
from pathlib import Path

import pytest
from llm_tool_cli.core import settings
from llm_tool_cli.core.entities import ToolName
from llm_tool_cli.core.result import Err, Ok, Result
from pytest_mock import MockerFixture

from vcsinstall.cli.commands.tests.helpers import (
    invoke_add,
    invoke_dependency_outdated,
    invoke_dependency_push,
    invoke_dependency_update,
    invoke_entry_add,
    invoke_install,
)
from vcsinstall.core import errors as core_errors
from vcsinstall.domain import RepositoryLocator
from vcsinstall.hosting import GitHubHostingAdapter, errors
from vcsinstall.hosting.adapters import github
from vcsinstall.vcs_clients import GitClient, clone_mirror
from vcsinstall.vcs_clients.tests.helpers import create_repository


class GitHubService:
    """Controlled responses at the hosted API boundary; native Git delivery remains real."""

    __slots__ = (
        "source",
        "fork",
        "writable",
        "review",
        "lost_response",
        "calls",
        "network",
        "forks_allowed",
        "malformed_review",
        "ambiguous_review",
    )

    def __init__(self, source: GitClient, fork: Path) -> None:
        self.source = source
        self.fork = fork
        self.writable = True
        self.review: dict[str, object] | None = None
        self.lost_response = False
        self.calls: list[tuple[str, str]] = []
        self.network = 1
        self.forks_allowed = True
        self.malformed_review = False
        self.ambiguous_review = False

    def repository(self, name: str) -> dict[str, object]:
        fork = name == "contributor/shared"
        values: dict[str, object] = {
            "id": 2 if fork else 1,
            "full_name": name,
            "owner": {"login": "contributor" if fork else "team"},
            "clone_url": str(self.fork) if fork else str(self.source.path),
            "ssh_url": str(self.fork) if fork else str(self.source.path),
            "permissions": {"push": fork or self.writable},
            "allow_forking": self.forks_allowed,
        }
        if fork:
            values["source"] = {"id": self.network, "full_name": "team/shared"}
        return values

    def create_fork(self) -> None:
        if not self.fork.exists():
            clone_mirror(RepositoryLocator(str(self.source.path)), self.fork).unwrap()
            GitClient(path=self.fork, repository=RepositoryLocator(str(self.fork))).set_config(
                "remote.origin.url", "https://github.com/contributor/shared.git"
            ).unwrap()

    def request(  # noqa: CCR001 — Model API reads, writes, and lost responses.
        self,
        endpoint: str,
        *,
        host: str,
        method: str = "GET",
        payload: dict[str, str] | None = None,
        paginate: bool = False,
    ) -> Result[bytes]:
        assert host == "github.com"
        self.calls.append((method, endpoint))
        if endpoint == "user":
            return Ok(json.dumps({"login": "contributor"}).encode())
        if endpoint in ("repos/team/shared", "repos/contributor/shared"):
            return Ok(json.dumps(self.repository(endpoint.removeprefix("repos/"))).encode())
        if endpoint.startswith("repos/team/shared/forks?"):
            return Ok(json.dumps([[self.repository("contributor/shared")] if self.fork.exists() else []]).encode())
        if endpoint == "repos/team/shared/forks" and method == "POST":
            self.create_fork()
            return Ok(json.dumps(self.repository("contributor/shared")).encode())
        if endpoint.startswith("repos/team/shared/pulls?"):
            reviews = [self.review, self.review] if self.ambiguous_review else [self.review] if self.review else []
            return Ok(json.dumps([reviews]).encode())
        if endpoint == "repos/team/shared/pulls" and method == "POST":
            assert payload is not None
            owner, _, branch = payload["head"].partition(":")
            repository = self.fork if owner == "contributor" else self.source.path
            head = GitClient(path=repository, repository=RepositoryLocator(str(repository)))
            revision = head.resolve_commit(f"refs/heads/{branch}").unwrap()
            self.review = {
                "number": 7,
                "html_url": "https://github.com/team/shared/pull/7",
                "state": "open",
                "head": {
                    "ref": branch,
                    "sha": revision,
                    "repo": {"id": 2 if owner == "contributor" else 1, "full_name": f"{owner}/shared"},
                },
                "base": {
                    "ref": "wrong-base" if self.malformed_review else payload["base"],
                    "sha": self.source.resolve_commit("refs/heads/trunk").unwrap(),
                    "repo": {"id": 1, "full_name": "team/shared"},
                },
            }
            if self.lost_response:
                self.lost_response = False
                return Err([errors.ServiceFailed(host="github.com", reason="Review creation response lost")])
            return Ok(json.dumps(self.review).encode())
        raise AssertionError(f"Unexpected API request {method} {endpoint}")


@pytest.fixture
def github_project(tmp_path: Path, mocker: MockerFixture, isolated_settings: None) -> tuple[Path, Path, GitHubService]:
    settings.initialize(name=ToolName("vcsinstall"), config_filename=settings.ConfigFilename("vcsinstall.toml"))
    mocker.patch.dict(
        os.environ,
        {
            "GIT_AUTHOR_NAME": "Author",
            "GIT_AUTHOR_EMAIL": "author@example.com",
            "GIT_COMMITTER_NAME": "Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.com",
        },
    )
    source = create_repository(tmp_path / "source")
    source.set_config("remote.origin.url", "https://github.com/team/shared.git").unwrap()
    project = tmp_path / "project"
    project.mkdir()
    config = project / "vcsinstall.toml"
    config.write_text("version = 1\n")
    cache = tmp_path / "cache"
    assert invoke_add(config, cache, "ci", str(source.path))[0] == 0
    assert invoke_entry_add(config, cache, "ci@/guide.txt", "@/guide.txt")[0] == 0
    (project / "guide.txt").write_bytes(b"local contribution\n")
    service = GitHubService(source, tmp_path / "fork.git")
    mocker.patch("vcsinstall.hosting.adapters.github._request", side_effect=service.request)
    return config, cache, service


class TestGitHubHostingAdapter:
    @pytest.mark.parametrize("writable", [True, False])
    def test_pr_uses_exact_source_target_and_writable_source_or_fork(
        self, github_project: tuple[Path, Path, GitHubService], writable: bool
    ) -> None:
        config, cache, service = github_project
        service.writable = writable
        before = config.with_name("vcsinstall.lock").read_bytes()
        base = service.source.resolve_commit("HEAD").unwrap()
        (service.source.path / "unrelated").write_text("new upstream")
        service.source.add(".").unwrap()
        service.source.commit("Source advances").unwrap()
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "pr", "--branch", "contribute/guide", "-m", "Improve guide"
        )
        assert status == 0 and records[0]["review_url"] == "https://github.com/team/shared/pull/7"
        assert records[0]["base_branch"] == "trunk" and records[0]["hosting_provider"] == "github"
        recipient = (
            service.source
            if writable
            else GitClient(path=service.fork, repository=RepositoryLocator(str(service.fork)))
        )
        revision = recipient.resolve_commit("refs/heads/contribute/guide").unwrap()
        assert recipient.inspect_commit(revision).unwrap().parents == (base,)
        assert records[0]["push_repository"] == str(recipient.path)
        assert config.with_name("vcsinstall.lock").read_bytes() == before
        assert (
            not any(method == "POST" and endpoint.endswith("/forks") for method, endpoint in service.calls)
            if writable
            else service.fork.exists()
        )

    def test_lost_review_response_is_recovered_using_verified_branch(
        self, github_project: tuple[Path, Path, GitHubService]
    ) -> None:
        config, cache, service = github_project
        service.writable = False
        service.lost_response = True
        arguments = ["ci", "--mode", "pr", "--branch", "contribute/retry", "-m", "Retry message"]
        status, failed, _ = invoke_dependency_push(config, cache, *arguments)
        assert status == 3 and failed[0]["code"] == errors.PublicationFailed.model_fields["code"].default
        assert failed[0]["branch"] == "contribute/retry" and failed[0]["base_branch"] == "trunk"
        assert str(service.fork) in str(failed[0]["completed_effects"])
        revision = (
            GitClient(path=service.fork, repository=RepositoryLocator(str(service.fork)))
            .resolve_commit("refs/heads/contribute/retry")
            .unwrap()
        )
        status, records, _ = invoke_dependency_push(config, cache, *arguments)
        assert status == 0 and records[0]["push_revision"] == revision
        assert records[0]["review_url"] == "https://github.com/team/shared/pull/7"
        assert sum(method == "POST" and endpoint.endswith("/pulls") for method, endpoint in service.calls) == 1
        assert sum(method == "POST" and endpoint.endswith("/forks") for method, endpoint in service.calls) == 1

    def test_conflicting_retry_does_not_replace_existing_branch(
        self, github_project: tuple[Path, Path, GitHubService]
    ) -> None:
        config, cache, service = github_project
        service.source.create_branch("contribute/conflict").unwrap()
        before = service.source.resolve_commit("refs/heads/contribute/conflict").unwrap()
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "pr", "--branch", "contribute/conflict"
        )
        assert status == 3 and any(
            record["code"] == errors.BranchExists.model_fields["code"].default for record in records
        )
        assert service.source.resolve_commit("refs/heads/contribute/conflict").unwrap() == before
        assert not any(method == "POST" for method, _ in service.calls)

    @pytest.mark.parametrize("state", ["closed", "merged", "conflicting", "ambiguous"])
    def test_existing_review_failure_reports_published_head_and_never_duplicates(
        self, github_project: tuple[Path, Path, GitHubService], state: str
    ) -> None:
        config, cache, service = github_project
        arguments = ["ci", "--mode", "pr", "--branch", "contribute/existing"]
        assert invoke_dependency_push(config, cache, *arguments)[0] == 0
        assert service.review is not None
        if state in ("closed", "merged"):
            service.review["state"] = "closed"
            service.review["merged_at"] = "2026-01-01T00:00:00Z" if state == "merged" else None
        elif state == "conflicting":
            service.review["base"] = {"ref": "another", "sha": "a" * 40, "repo": {"id": 1, "full_name": "team/shared"}}
        else:
            service.ambiguous_review = True
        count = len([call for call in service.calls if call == ("POST", "repos/team/shared/pulls")])
        status, records, _ = invoke_dependency_push(config, cache, *arguments)
        assert status == 3 and records[0]["completed_effects"]
        assert len([call for call in service.calls if call == ("POST", "repos/team/shared/pulls")]) == count

    def test_created_review_must_confirm_matching_head_and_target(
        self, github_project: tuple[Path, Path, GitHubService]
    ) -> None:
        config, cache, service = github_project
        service.malformed_review = True
        status, records, _ = invoke_dependency_push(
            config, cache, "ci", "--mode", "pr", "--branch", "contribute/mismatch"
        )
        assert status == 3 and records[0]["code"] == errors.PublicationFailed.model_fields["code"].default
        assert all(record["type"] == "error" for record in records)

    @pytest.mark.parametrize("target", ["unrelated", "missing"])
    def test_target_failure_happens_before_fork_creation(
        self, github_project: tuple[Path, Path, GitHubService], target: str
    ) -> None:
        config, cache, service = github_project
        service.writable = False
        if target == "unrelated":
            service.source.run(["checkout", "--orphan", "unrelated", "--"]).unwrap()
            service.source.commit("Different root").unwrap()
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "pr", "--base", target)
        assert status == 3 and records[0]["code"] == errors.PreparationFailed.model_fields["code"].default
        assert ("not an ancestor" if target == "unrelated" else "does not exist") in str(records[0]["reason"])
        assert not service.fork.exists() and not any(method == "POST" for method, _ in service.calls)

    @pytest.mark.parametrize("recipient", ["eligible", "unrelated-network", "other-host"])
    def test_explicit_fork_requires_same_host_and_source_network(
        self, github_project: tuple[Path, Path, GitHubService], recipient: str
    ) -> None:
        config, cache, service = github_project
        service.create_fork()
        if recipient == "unrelated-network":
            service.network = 999
        elif recipient == "other-host":
            GitClient(path=service.fork, repository=RepositoryLocator(str(service.fork))).set_config(
                "remote.origin.url", "https://example.com/contributor/shared.git"
            ).unwrap()
        status, records, _ = invoke_dependency_push(
            config,
            cache,
            "ci",
            "--mode",
            "pr",
            "--push-repository",
            str(service.fork),
            "--branch",
            "contribute/explicit",
        )
        if recipient == "eligible":
            assert status == 0 and records[0]["push_repository"] == str(service.fork)
            assert records[0]["review_url"] == "https://github.com/team/shared/pull/7"
            assert not any(method == "POST" and endpoint.endswith("/forks") for method, endpoint in service.calls)
            return
        assert status == 3 and records[0]["code"] == errors.PreparationFailed.model_fields["code"].default
        assert not any(method == "POST" for method, _ in service.calls)

    def test_new_ineligible_fork_is_reported_as_a_completed_effect(
        self, github_project: tuple[Path, Path, GitHubService]
    ) -> None:
        config, cache, service = github_project
        service.writable = False
        service.network = 999

        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "pr", "--branch", "contribute/fork")

        assert status == 3 and records[0]["code"] == errors.PublicationFailed.model_fields["code"].default
        assert records[0]["completed_effects"] == ["Created fork github.com/contributor/shared"]
        assert service.fork.exists() and service.review is None
        recipient = GitClient(path=service.fork, repository=RepositoryLocator(str(service.fork)))
        assert recipient.remote_branch(RepositoryLocator(str(service.fork)), "contribute/fork").unwrap() is None

    def test_existing_eligible_fork_is_reused(self, github_project: tuple[Path, Path, GitHubService]) -> None:
        config, cache, service = github_project
        service.writable = False
        service.create_fork()
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "pr")
        assert status == 0 and records[0]["push_repository"] == str(service.fork)
        assert not any(method == "POST" and endpoint.endswith("/forks") for method, endpoint in service.calls)

    def test_forbidden_fork_does_not_fall_back_to_another_recipient(
        self, github_project: tuple[Path, Path, GitHubService]
    ) -> None:
        config, cache, service = github_project
        service.writable = False
        service.forks_allowed = False
        status, records, _ = invoke_dependency_push(config, cache, "ci", "--mode", "pr")
        assert status == 3 and "forbidden" in str(records)
        assert not service.fork.exists() and not any(method == "POST" for method, _ in service.calls)

    @pytest.mark.parametrize("provider", [None, "git", "github"])
    @pytest.mark.parametrize("mode", ["branch", "direct", "patch"])
    def test_native_modes_require_no_service_access(
        self, github_project: tuple[Path, Path, GitHubService], mocker: MockerFixture, provider: str | None, mode: str
    ) -> None:
        config, cache, service = github_project
        mocker.patch.object(github, "_run", side_effect=AssertionError("Native delivery must not use gh"))
        arguments = ["ci", "--mode", mode]
        if provider is not None:
            arguments.extend(["--hosting-provider", provider])
        if mode == "direct":
            service.create_fork()
            arguments.extend(["--push-repository", str(service.fork)])
        if mode == "patch":
            arguments.extend(["--output", str(config.parent / "contribution.patch")])

        status, records, _ = invoke_dependency_push(config, cache, *arguments)

        assert status == 0 and records[0]["hosting_provider"] == (provider or "github") and not service.calls

    @pytest.mark.parametrize(
        "command",
        ["dependency add", "entry add", "entry add relative", "dependency outdated", "dependency update", "install"],
    )
    def test_acquisition_commands_select_github_without_service_access(
        self,
        github_project: tuple[Path, Path, GitHubService],
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
        command: str,
    ) -> None:
        config, cache, service = github_project
        if command == "entry add relative":
            config.write_text(config.read_text().replace(str(service.source.path), "../source"))
            nested = config.parent / "nested"
            nested.mkdir()
            monkeypatch.chdir(nested)
        mocker.patch.object(github, "_run", side_effect=AssertionError("Acquisition must not use gh"))
        acquire = mocker.patch.object(
            GitHubHostingAdapter, "acquire", autospec=True, side_effect=GitHubHostingAdapter.acquire
        )
        destination = config.parent / "guide.txt"
        destination.write_bytes(service.source.run(["show", "HEAD:guide.txt"]).unwrap())

        if command == "dependency add":
            status, _, _ = invoke_add(config, cache, "another", str(service.source.path))
        elif command in ("entry add", "entry add relative"):
            status, _, _ = invoke_entry_add(config, cache, "ci@/guide.txt", "@/another.txt")
        elif command == "dependency outdated":
            status, _, _ = invoke_dependency_outdated(config, cache, "ci")
        elif command == "dependency update":
            status, _, _ = invoke_dependency_update(config, cache, "ci")
        else:
            destination.unlink()
            status, _, _ = invoke_install(config, cache, "--locked", "--force")

        assert status == 0 and not service.calls
        acquire.assert_called_once()


class TestInferGithub:
    @pytest.mark.parametrize("locator", ["https://github.com/team/shared.git", "git@github.com:team/shared.git"])
    def test_public_host_requires_no_service_client(self, locator: str, mocker: MockerFixture) -> None:
        mocker.patch.object(github, "_run", side_effect=AssertionError("Public host identity is local syntax"))
        assert github.infer_github(RepositoryLocator(locator)).unwrap()

    def test_ssh_alias_and_enterprise_host_use_exact_client_configuration(self, mocker: MockerFixture) -> None:
        client = mocker.patch.object(
            github,
            "_run",
            side_effect=[
                Ok(b"hostname github.company.test\n"),
                Ok(b"configured-user\n"),
                Err([errors.ServiceFailed(host="github.company.test", reason="No global user")]),
            ],
        )
        assert github.infer_github(RepositoryLocator("git@company-alias:team/shared.git")).unwrap()
        assert client.call_args_list[1].args[0] == ["gh", "config", "get", "user", "--host", "github.company.test"]
        assert client.call_args_list[2].args[0] == ["gh", "config", "get", "user"]

    def test_unrelated_git_host_is_not_github(self, mocker: MockerFixture) -> None:
        mocker.patch.object(github, "_run", return_value=Ok(b"global-user\n"))
        assert not github.infer_github(RepositoryLocator("https://git.example.test/team/shared.git")).unwrap()

    @pytest.mark.parametrize(
        "users,expected",
        [([b"", b""], False), ([b"global\n", b"global\n"], False), ([b"host-user\n", b"global\n"], True)],
    )
    def test_only_host_specific_configuration_identifies_custom_host(
        self, users: list[bytes], expected: bool, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(github, "_run", side_effect=[Ok(user) for user in users])

        assert github.infer_github(RepositoryLocator("https://git.example.test/team/shared.git")).unwrap() == expected

    def test_exact_gh_host_environment_requires_no_service_client(self, mocker: MockerFixture) -> None:
        mocker.patch.dict(os.environ, {"GH_HOST": "github.company.test"})
        mocker.patch.object(github, "_run", side_effect=AssertionError("Configured host requires no service access"))

        assert github.infer_github(RepositoryLocator("https://github.company.test/team/shared.git")).unwrap()

    def test_unexpected_internal_errors_are_not_environment_diagnostics(self, mocker: MockerFixture) -> None:
        mocker.patch.object(github, "_run", side_effect=core_errors.InternalError("Unexpected bug"))
        with pytest.raises(core_errors.InternalError):
            github.infer_github(RepositoryLocator("https://git.example.test/team/shared.git"))
