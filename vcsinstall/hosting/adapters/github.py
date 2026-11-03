"""GitHub service workflows composed with native Git publication."""

import json
import os
import re
import subprocess  # noqa: S404 — Native clients receive argument arrays without a shell.
from contextlib import AbstractContextManager
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import pydantic
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import ResolvedProjectPath

from vcsinstall.cache import CacheRootPath
from vcsinstall.domain import Dependency, RepositoryLocator, SelectorValue, SourceRevision
from vcsinstall.hosting import errors
from vcsinstall.hosting.adapters.git import (
    GitHostingAdapter,
    prepare_tree,
    publish_branch,
    target_branch,
    validate_git_branch,
    validate_recipient,
    verify_locked_content,
)
from vcsinstall.hosting.contributions import covering_changes
from vcsinstall.hosting.entities import DeliveryMode, DeliveryResult, HostingProvider, PublicationRequest, PushOptions
from vcsinstall.sources import SourceAdapter
from vcsinstall.vcs_clients import GitClient, GitObjectId


# These external API shapes intentionally ignore unrelated GitHub response fields.
class _User(BaseEntity):
    model_config = pydantic.ConfigDict(extra="ignore")
    login: str


class _Permissions(BaseEntity):
    model_config = pydantic.ConfigDict(extra="ignore")
    push: bool = False


class _RepositoryLink(BaseEntity):
    model_config = pydantic.ConfigDict(extra="ignore")
    id: int
    full_name: str


class _Repository(_RepositoryLink):
    owner: _User
    clone_url: RepositoryLocator
    ssh_url: RepositoryLocator
    permissions: _Permissions = pydantic.Field(default_factory=_Permissions)
    source: _RepositoryLink | None = None
    parent: _RepositoryLink | None = None
    allow_forking: bool = True

    @property
    def network(self) -> int:
        return self.source.id if self.source else self.parent.id if self.parent else self.id


class _ReviewRef(BaseEntity):
    model_config = pydantic.ConfigDict(extra="ignore")
    ref: str
    sha: str
    repo: _RepositoryLink | None


class _Review(BaseEntity):
    model_config = pydantic.ConfigDict(extra="ignore")
    number: int
    html_url: str
    state: str
    merged_at: str | None = None
    head: _ReviewRef
    base: _ReviewRef


class _Identity(BaseEntity):
    host: str
    name: str


def _run(command: list[str], *, host: str, input: bytes | None = None) -> Result[bytes]:
    try:
        result = subprocess.run(  # noqa: S603 — gh and SSH configuration queries are noninteractive argument arrays.
            command,
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, GH_PROMPT_DISABLED="1", GH_PAGER="cat"),
            check=False,
        )
    except OSError as exc:
        return Err([errors.ServiceFailed(host=host, reason=str(exc)).with_cause(exc)])
    if result.returncode:
        return Err(
            [
                errors.ServiceFailed(
                    host=host,
                    reason=result.stderr.decode("utf-8", errors="replace").strip()
                    or f"Client exited with {result.returncode}.",
                )
            ]
        )
    return Ok(result.stdout)


def _decode[T](data: bytes, adapter: pydantic.TypeAdapter[T], *, host: str) -> Result[T]:
    try:
        return Ok(adapter.validate_json(data))
    except pydantic.ValidationError as exc:
        return Err([errors.ServiceFailed(host=host, reason=f"Invalid service response: {exc}").with_cause(exc)])


@unwrap_to_error
def _identity(  # noqa: CCR001 — Resolve local remotes, native URL syntax, and SSH aliases at the identity boundary.
    repository: RepositoryLocator,
) -> Result[_Identity | None]:
    if Path(repository).is_absolute():
        client = GitClient(path=Path(repository), repository=repository)
        remotes = client.run(["remote", "-v"]).unwrap().decode("utf-8", errors="replace")
        locators = {line.split()[1] for line in remotes.splitlines() if line.endswith("(fetch)")}
        identities = [
            _identity(RepositoryLocator(locator)).unwrap()
            for locator in sorted(locators)
            if not Path(locator).is_absolute()
        ]
        unique = {(identity.host, identity.name) for identity in identities if identity is not None}
        return Ok(_Identity(host=next(iter(unique))[0], name=next(iter(unique))[1]) if len(unique) == 1 else None)
    ssh = False
    if "://" in repository:
        try:
            url = urlsplit(repository)
            host, path = url.hostname, url.path
            ssh = url.scheme == "ssh"
        except ValueError:
            return Ok(None)
    else:
        match = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", repository)
        if match is None:
            return Ok(None)
        host, path = match.groups()
        ssh = True
    if host is None:
        return Ok(None)
    if ssh and host.lower() != "github.com":
        configuration = _run(["ssh", "-G", "--", host], host=host).unwrap()
        names = [
            line.split(maxsplit=1)[1].decode("utf-8")
            for line in configuration.splitlines()
            if line.startswith(b"hostname ")
        ]
        if len(names) != 1:
            return Ok(None)
        host = names[0]
    name = path.strip("/").removesuffix(".git")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name) is None:
        return Ok(None)
    return Ok(_Identity(host=host.lower(), name=name))


@unwrap_to_error
def infer_github(repository: RepositoryLocator) -> Result[bool]:
    """Recognize github.com or an exact configured gh host, including resolved SSH aliases.

    A Git URL on an unrelated host is not assumed to be GitHub.
    """
    identity = _identity(repository).unwrap()
    if identity is None:
        return Ok(False)
    if identity.host in ("github.com", os.environ.get("GH_HOST", "").lower()):
        return Ok(True)
    # Query only local configuration, without testing credentials or contacting a service.
    user = _run(["gh", "config", "get", "user", "--host", identity.host], host=identity.host).unwrap().strip()
    global_user = _run(["gh", "config", "get", "user"], host=identity.host).unwrap_or(b"").strip()
    # gh falls back to global settings: an inherited user does not identify this host.
    return Ok(bool(user) and user != global_user)


def _request(
    endpoint: str, *, host: str, method: str = "GET", payload: dict[str, str] | None = None, paginate: bool = False
) -> Result[bytes]:
    command = ["gh", "api", "--hostname", host, "--method", method]
    if paginate:
        command += ["--paginate", "--slurp"]
    if payload is not None:
        command += ["--input", "-"]
    command += [endpoint]
    return _run(command, host=host, input=json.dumps(payload).encode("utf-8") if payload is not None else None)


@unwrap_to_error
def _get_repository(name: str, *, host: str) -> Result[_Repository]:
    return _decode(_request(f"repos/{name}", host=host).unwrap(), pydantic.TypeAdapter(_Repository), host=host)


@unwrap_to_error
def _get_account(*, host: str) -> Result[_User]:
    return _decode(_request("user", host=host).unwrap(), pydantic.TypeAdapter(_User), host=host)


@unwrap_to_error
def _list_forks(source: _Repository, *, host: str) -> Result[list[_RepositoryLink]]:
    pages = _decode(
        _request(f"repos/{source.full_name}/forks?per_page=100", host=host, paginate=True).unwrap(),
        pydantic.TypeAdapter(list[list[_RepositoryLink]]),
        host=host,
    ).unwrap()
    return Ok([repository for page in pages for repository in page])


@unwrap_to_error
def _request_fork(source: _Repository, *, host: str) -> Result[_Repository]:
    return _decode(
        _request(f"repos/{source.full_name}/forks", host=host, method="POST", payload={}).unwrap(),
        pydantic.TypeAdapter(_Repository),
        host=host,
    )


@unwrap_to_error
def _list_reviews(source: _Repository, head: _Repository, branch: str, *, host: str) -> Result[list[_Review]]:
    query = urlencode({"state": "all", "head": f"{head.owner.login}:{branch}", "per_page": "100"})
    pages = _decode(
        _request(f"repos/{source.full_name}/pulls?{query}", host=host, paginate=True).unwrap(),
        pydantic.TypeAdapter(list[list[_Review]]),
        host=host,
    ).unwrap()
    return Ok([review for page in pages for review in page])


@unwrap_to_error
def _create_review(
    source: _Repository, head: _Repository, request: PublicationRequest, *, host: str
) -> Result[_Review]:
    options = request.options
    assert options.branch is not None and options.base is not None
    lines = options.message.splitlines()
    title = next(line for line in lines if line.strip())[:256]
    body = options.message + "\n\nSource paths:\n" + "\n".join(f"- {change.source_path}" for change in request.changes)
    payload = {
        "title": title,
        "body": body,
        "head": f"{head.owner.login}:{options.branch}",
        "head_repo": head.full_name.split("/")[1],
        "base": options.base,
    }
    data = _request(f"repos/{source.full_name}/pulls", host=host, method="POST", payload=payload).unwrap()
    return _decode(data, pydantic.TypeAdapter(_Review), host=host)


def _eligible(source: _Repository, recipient: _Repository) -> bool:
    """Require source identity or a metadata-established fork network and writable permissions.

    Matching basenames alone never establish eligibility for a review head.
    """
    return recipient.permissions.push and (recipient.id == source.id or recipient.network == source.network)


@unwrap_to_error
def _explicit_recipient(host: str, origin: _Repository, repository: RepositoryLocator) -> Result[_Repository]:
    """Resolve an explicit recipient on the source host and in its writable fork network."""
    identity = _identity(repository).unwrap()
    if identity is None or identity.host != host:
        return Err(
            [
                errors.PreparationFailed(
                    repository=repository,
                    reason="A PR recipient must identify an eligible repository on the source's GitHub host.",
                )
            ]
        )
    head = _get_repository(identity.name, host=host).unwrap()
    if not _eligible(origin, head):
        return Err(
            [
                errors.PreparationFailed(
                    repository=repository,
                    reason="The selected PR recipient is not an eligible writable head in the source's fork network.",
                )
            ]
        )
    return Ok(head)


@unwrap_to_error
def _create_fork(
    host: str, origin: _Repository, account: _User, request: PublicationRequest, effects: list[str]
) -> Result[_Repository]:
    """Create an allowed fork, record the effect, and confirm its owner and writability."""
    if not origin.allow_forking:
        return Err(
            [
                errors.PreparationFailed(
                    repository=request.dependency.repository,
                    reason="Fork creation is forbidden; supply an eligible writable --push-repository.",
                )
            ]
        )
    created = _request_fork(origin, host=host)
    if created.is_err():
        return Err(
            [
                errors.PublicationFailed(
                    repository=request.dependency.repository,
                    branch=request.options.branch,
                    base_branch=request.options.base,
                    reason=(
                        "Fork creation failed or its response was lost; "
                        "inspect the configured account's forks before retrying."
                    ),
                    uncertain=True,
                ),
                *created.unwrap_err(),
            ]
        )
    head = created.unwrap()
    effects.append(f"Created fork {host}/{head.full_name}")
    head = _get_repository(head.full_name, host=host).unwrap()
    if head.owner.login.lower() != account.login.lower() or not _eligible(origin, head):
        return Err(
            [
                errors.PreparationFailed(
                    repository=request.dependency.repository,
                    reason="The created fork is not an eligible writable repository for the configured account.",
                )
            ]
        )
    return Ok(head)


@unwrap_to_error
def _select_fork(
    host: str, origin: _Repository, account: _User, request: PublicationRequest, effects: list[str]
) -> Result[_Repository]:
    """Reuse the account's sole eligible fork or create one when no candidate exists."""
    candidates = []
    for fork in _list_forks(origin, host=host).unwrap():
        if fork.full_name.split("/")[0].lower() != account.login.lower():
            continue
        candidate = _get_repository(fork.full_name, host=host).unwrap()
        if _eligible(origin, candidate):
            candidates.append(candidate)
    if len(candidates) > 1:
        return Err(
            [
                errors.PreparationFailed(
                    repository=request.dependency.repository,
                    reason="Several writable forks are eligible; supply --push-repository explicitly.",
                )
            ]
        )
    if candidates:
        return Ok(candidates[0])
    return _create_fork(host, origin, account, request, effects)


@unwrap_to_error
def _select_recipient(
    host: str, origin: _Repository, account: _User, request: PublicationRequest, effects: list[str]
) -> Result[
    tuple[_Repository, RepositoryLocator]
]:  # noqa: TAE002 — The service identity and native locator are resolved together.
    override = request.options.push_repository
    if override is not None:
        return Ok((_explicit_recipient(host, origin, override).unwrap(), override))
    if origin.permissions.push:
        return Ok((origin, request.dependency.repository))

    head = _select_fork(host, origin, account, request, effects).unwrap()
    ssh = (
        request.dependency.repository.startswith("ssh://")
        or "://" not in request.dependency.repository
        and not Path(request.dependency.repository).is_absolute()
    )
    return Ok((head, head.ssh_url if ssh else head.clone_url))


def _matching_review(
    review: _Review, origin: _Repository, head: _Repository, request: PublicationRequest, revision: GitObjectId
) -> bool:
    """Confirm exact head repository, branch, and commit and exact source target.

    For example, the same branch name in another fork or targeting another base does not match.
    """
    return (
        review.head.repo is not None
        and review.head.repo.id == head.id
        and review.head.ref == request.options.branch
        and review.head.sha == revision
        and review.base.repo is not None
        and review.base.repo.id == origin.id
        and review.base.ref == request.options.base
        and bool(review.html_url)
    )


@unwrap_to_error
def _validate_target(client: GitClient, parent: GitObjectId, request: PublicationRequest) -> Result[None]:
    """Require an existing source target containing the locked commit, retrieving full history.

    A descendant target passes; a missing branch or an unrelated root fails before publication.
    """
    repository, base = request.dependency.repository, request.options.base
    assert base is not None
    target = client.remote_branch(repository, base).unwrap()
    if target is None:
        return Err(
            [errors.PreparationFailed(repository=repository, reason=f"PR target branch {base!r} does not exist.")]
        )
    client.fetch_branch(repository, base).unwrap()
    if client.run(["rev-parse", "--is-shallow-repository"]).unwrap().strip() == b"true":
        client.run(["fetch", "--unshallow", "--no-tags", "--", repository, f"refs/heads/{base}"]).unwrap()
    if not client.is_ancestor(parent, target).unwrap():
        return Err(
            [
                errors.PreparationFailed(
                    repository=repository,
                    reason=f"Locked commit {parent} is not an ancestor of target {base} at {target}.",
                )
            ]
        )
    return Ok(None)


@unwrap_to_error
def _publish_head(
    client: GitClient,
    repository: RepositoryLocator,
    request: PublicationRequest,
    *,
    parent: GitObjectId,
    tree: GitObjectId,
) -> Result[GitObjectId]:
    """Publish a new branch or reuse an existing commit with identical contribution content."""
    branch = request.options.branch
    assert branch is not None
    revision = client.remote_branch(repository, branch).unwrap()
    if revision is None:
        revision = client.commit_tree(tree, parent, request.options.message).unwrap()
        publish_branch(client, repository, branch, revision, expected=None).unwrap()
        return Ok(revision)

    client.fetch_branch(repository, branch).unwrap()
    existing = client.inspect_commit(revision).unwrap()
    expected_message = request.options.message.encode("utf-8")
    if not expected_message.endswith(b"\n"):
        expected_message += b"\n"
    if existing.tree != tree or existing.parents != (parent,) or existing.message != expected_message:
        return Err([errors.BranchExists(repository=repository, branch=branch)])
    return Ok(revision)


def _validate_open_review(review: _Review, repository: RepositoryLocator) -> Result[None]:
    """Require a matching review to remain open before returning it as a completed delivery.

    An open review passes; a closed or merged review fails with its URL and actual state.
    """
    if review.state == "open":
        return Ok(None)
    state = "merged" if review.merged_at else review.state
    return Err(
        [
            errors.PreparationFailed(
                repository=repository,
                reason=(
                    f"Matching review {review.html_url} is {state}; "
                    "inspect it before submitting another contribution."
                ),
            )
        ]
    )


@unwrap_to_error
def _resolve_review(
    host: str,
    origin: _Repository,
    head: _Repository,
    repository: RepositoryLocator,
    request: PublicationRequest,
    revision: GitObjectId,
) -> Result[_Review]:
    """Recover an exact open review or create and confirm one after branch publication."""
    assert request.options.branch is not None
    reviews = _list_reviews(origin, head, request.options.branch, host=host).unwrap()
    if len(reviews) > 1 or any(not _matching_review(review, origin, head, request, revision) for review in reviews):
        return Err(
            [
                errors.PreparationFailed(
                    repository=repository,
                    reason="Existing reviews are ambiguous or conflict with the requested head or target.",
                )
            ]
        )
    if reviews:
        review = reviews[0]
        _validate_open_review(review, repository).unwrap()
        return Ok(review)

    review = _create_review(origin, head, request, host=host).unwrap()
    if review.state != "open" or not _matching_review(review, origin, head, request, revision):
        return Err(
            [
                errors.PreparationFailed(
                    repository=repository, reason="GitHub did not confirm a matching open review and URL."
                )
            ]
        )
    return Ok(review)


class GitHubHostingAdapter:
    __slots__ = ("git",)

    def __init__(self, *, root: CacheRootPath) -> None:
        self.git = GitHostingAdapter(root=root)

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        return self.git.probe_repository(repository)

    def validate_artifact(self, dependency: Dependency, path: ResolvedProjectPath | None) -> Result[None]:
        """Retain native artifact protection without GitHub service access.

        For example, a patch inside the source's Git administration fails locally.
        """
        return self.git.validate_artifact(dependency, path)

    def acquire(
        self, dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> AbstractContextManager[Result[Path]]:
        return self.git.acquire(dependency, revision=revision, refresh=refresh)

    @unwrap_to_error
    def validate_options(self, dependency: Dependency, options: PushOptions) -> Result[None]:
        """Support PR and native Git modes, requiring a PR base for non-branch pins.

        A branch-tracking PR needs no --base; a tag-pinned PR requires one.
        """
        if options.mode != DeliveryMode.pr:
            return self.git.validate_options(dependency, options)
        for branch in (options.branch, options.base):
            if branch is not None:
                validate_git_branch(dependency, branch).unwrap()
        if dependency.selector is not None:
            target_branch(dependency, options.base, option="--base").unwrap()
        return Ok(None)

    @unwrap_to_error
    def publish(self, request: PublicationRequest, *, source: SourceAdapter) -> Result[DeliveryResult]:
        if request.options.mode != DeliveryMode.pr:
            return self.git.publish(request, source=source).map(
                lambda result: result.replace(provider=HostingProvider.github)
            )
        base = target_branch(request.dependency, request.options.base, option="--base").unwrap()
        branch = request.options.branch or SelectorValue("vcsinstall/" + uuid4().hex)
        request = request.replace(options=request.options.replace(base=base, branch=branch))
        effects: list[str] = []
        result = self._publish_pr(request, source=source, effects=effects)
        if result.is_err() and effects:
            return Err(
                [
                    errors.PublicationFailed(
                        repository=request.dependency.repository,
                        branch=branch,
                        base_branch=base,
                        reason="GitHub review delivery did not complete.",
                        completed_effects=effects,
                        uncertain=True,
                    ),
                    *result.unwrap_err(),
                ]
            )
        return result

    @unwrap_to_error
    def _publish_pr(
        self, request: PublicationRequest, *, source: SourceAdapter, effects: list[str]
    ) -> Result[DeliveryResult]:
        identity = _identity(request.dependency.repository).unwrap()
        if identity is None:
            return Err(
                [
                    errors.PreparationFailed(
                        repository=request.dependency.repository,
                        reason=(
                            "Cannot identify the exact GitHub source; "
                            "configure an unambiguous source host and repository."
                        ),
                    )
                ]
            )
        host = identity.host
        origin = _get_repository(identity.name, host=host).unwrap()
        account = _get_account(host=host).unwrap()

        with self.git.acquire(request.dependency, revision=request.revision, refresh=False) as acquired:
            path = acquired.unwrap()
            client = GitClient(path=path, repository=request.dependency.repository)
            parent = client.resolve_commit(request.revision).unwrap()
            _validate_target(client, parent, request).unwrap()
            verify_locked_content(request, path, source=source).unwrap()
            tree = prepare_tree(client, parent, request, covering_changes(request.changes)).unwrap()

            head, repository = _select_recipient(host, origin, account, request, effects).unwrap()
            validate_recipient(request, repository).unwrap()
            revision = _publish_head(client, repository, request, parent=parent, tree=tree).unwrap()
            effects.append(
                f"Published {repository} branch {request.options.branch} at {revision}; "
                f"review target {identity.host}/{origin.full_name}:{request.options.base}"
            )

            review = _resolve_review(host, origin, head, repository, request, revision).unwrap()
            return Ok(
                DeliveryResult(
                    provider=HostingProvider.github,
                    repository=repository,
                    branch=request.options.branch,
                    revision=SourceRevision(revision),
                    base_branch=request.options.base,
                    review_url=review.html_url,
                )
            )
