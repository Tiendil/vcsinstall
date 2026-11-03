from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import (
    ProjectPathId,
    ProjectRootPath,
    ResolvedProjectPath,
    project_path_id_from_resolved,
    resolve_inside_project,
    resolve_project_root,
)

from vcsinstall.cache import CacheRootPath, RepoCache
from vcsinstall.core.paths import join_path
from vcsinstall.domain import (
    ContentKind,
    Dependency,
    RepositoryLocator,
    SelectorKind,
    SelectorValue,
    SourcePath,
    SourceRevision,
    VersionSelector,
    urls,
)
from vcsinstall.filesystem import FSClient
from vcsinstall.hosting import errors as publication_errors
from vcsinstall.hosting.contributions import covering_changes
from vcsinstall.hosting.entities import (
    Contribution,
    DeliveryMode,
    DeliveryResult,
    HostingProvider,
    PublicationRequest,
    PushOptions,
)
from vcsinstall.sources import ResolvedSource, SourceAdapter, errors, get_adapter
from vcsinstall.vcs_clients import GitClient, GitObjectId, GitObjectKind, GitTreeEntry, clone_mirror


def target_branch(dependency: Dependency, explicit: SelectorValue | None, *, option: str) -> Result[SelectorValue]:
    """Resolve a literal destination or review branch from explicit intent or the effective branch selector."""
    if explicit is not None:
        return Ok(explicit)
    selector = dependency.selector
    if selector is not None and selector.kind == SelectorKind.branch:
        return Ok(selector.value)
    return Err([InvalidArguments(reason=f"A tag or revision pin requires an explicit {option} branch.")])


def validate_git_branch(dependency: Dependency, branch: SelectorValue) -> Result[None]:
    """Validate literal publication branch syntax through the source-owned Git rules.

    For example, 'contribute/docs' passes while 'HEAD' and 'bad..name' fail without invoking Git.
    """
    return get_adapter(dependency.vcs).validate_input(
        dependency.replace(selector=VersionSelector(kind=SelectorKind.branch, value=branch))
    )


@unwrap_to_error
def validate_recipient(
    request: PublicationRequest, repository: RepositoryLocator, *, branch: str | None = None
) -> Result[None]:
    """Protect the target project's repository and every checked-out local destination branch.

    A separate bare source passes; publishing to the target project's own repository or
    directly advancing a branch checked out in any recipient worktree fails.
    """
    if not Path(repository).is_absolute():
        return Ok(None)
    recipient = GitClient(path=Path(repository), repository=repository)
    recipient_directory = recipient.common_directory().unwrap()
    target_directory = (
        GitClient(path=request.local_root, repository=RepositoryLocator(str(request.local_root)))
        .common_directory()
        .unwrap()
    )
    if recipient_directory is not None and recipient_directory == target_directory:
        return Err(
            [
                publication_errors.PreparationFailed(
                    repository=repository,
                    reason="The publication recipient is the target project's own Git repository.",
                )
            ]
        )
    if branch is not None and f"refs/heads/{branch}" in recipient.checked_out_branches().unwrap():
        return Err(
            [
                publication_errors.PreparationFailed(
                    repository=repository,
                    reason=f"Destination branch {branch!r} is checked out in a recipient worktree.",
                )
            ]
        )
    return Ok(None)


def _contains_git_path(root: SourcePath, path: bytes) -> bool:
    r"""Compare a source scope against native Git path bytes without decoding names.

    For example, '@/docs' includes b'docs/\xff' but not b'docs-old/\xff';
    '@/' includes either path. Unselected non-UTF-8 names keep their literal identity.
    """
    prefix = root.removeprefix("@/").encode("utf-8")
    return not prefix or path == prefix or path.startswith(prefix + b"/")


@unwrap_to_error
def prepare_tree(  # noqa: CCR001 — Keep complete mapping replacement and native mode preservation in one plan.
    client: GitClient, parent: GitObjectId, request: PublicationRequest, changes: tuple[Contribution, ...]
) -> Result[GitObjectId]:
    """Replace complete selected mappings in the parent tree while retaining all other native objects."""
    entries = client.list_tree(parent).unwrap()
    retained = {
        entry.path: entry
        for entry in entries
        if not any(_contains_git_path(change.source_path, entry.path) for change in changes)
    }
    selected: dict[bytes, tuple[Contribution, ProjectPathId, bool]] = {}
    for change in changes:
        if change.source_path == "@/" and change.content.kind == ContentKind.file:
            return Err(
                [
                    publication_errors.PreparationFailed(
                        repository=client.repository, reason="The Git repository root cannot be replaced by a file."
                    )
                ]
            )
        for file in change.content.files:
            path = join_path(change.source_path, file.path).removeprefix("@/")
            if any(part.lower() == ".git" for part in path.split("/")):
                return Err(
                    [
                        publication_errors.PreparationFailed(
                            repository=client.repository,
                            reason=f"Git cannot represent selected administrative path {path!r}.",
                        )
                    ]
                )
            native = path.encode("utf-8")
            if any(
                native.startswith(other + b"/") and value.kind != GitObjectKind.tree
                for other, value in retained.items()
            ):
                return Err(
                    [
                        publication_errors.PreparationFailed(
                            repository=client.repository,
                            reason=f"An unselected object obstructs source path {path!r}.",
                        )
                    ]
                )
            selected[native] = (change, file.path, file.executable)

    original = {entry.path: entry for entry in entries}
    filesystem = FSClient(root=request.local_root)
    for native_path, (change, relative, executable) in selected.items():
        previous = original.get(native_path)
        mode = (
            previous.mode
            if previous is not None and previous.mode in (b"100644", b"100755")
            else b"100755" if executable else b"100644"
        )
        local = filesystem.resolve_path(join_path(change.destination, relative)).unwrap()
        retained[native_path] = GitTreeEntry(
            mode=mode, kind=GitObjectKind.blob, object_id=client.store_file(local).unwrap(), path=native_path
        )
    return client.write_tree(tuple(retained.values()))


@unwrap_to_error
def verify_locked_content(request: PublicationRequest, path: Path, *, source: SourceAdapter) -> Result[None]:
    """Require the locked source to reproduce the recorded contribution baselines.

    For example, an extra source file absent from a directory baseline fails instead of being hidden.
    """
    resolved = ResolvedSource(dependency=request.dependency, path=path, revision=request.revision)
    content = source.inspect(resolved, tuple(change.source_path for change in request.changes)).unwrap()
    for change in request.changes:
        if content[change.source_path].file_hashes != change.baseline:
            return Err(
                [
                    publication_errors.PreparationFailed(
                        repository=request.dependency.repository,
                        reason=(
                            f"Locked source content for {change.destination} "
                            "differs from its installation baseline."
                        ),
                    )
                ]
            )
    return Ok(None)


def publish_branch(
    client: GitClient,
    repository: RepositoryLocator,
    branch: SelectorValue,
    revision: GitObjectId,
    *,
    expected: GitObjectId | None,
) -> Result[None]:
    result = client.push_branch(repository, branch, revision, expected=expected)
    if result.is_err():
        return Err(
            [
                publication_errors.PublicationFailed(
                    repository=repository,
                    branch=branch,
                    revision=SourceRevision(revision),
                    uncertain=True,
                    reason="Git did not confirm branch publication; the recipient may have accepted the update.",
                ),
                *result.unwrap_err(),
            ]
        )
    return Ok(None)


class GitHostingAdapter:
    __slots__ = ("root",)

    def __init__(self, *, root: CacheRootPath) -> None:
        self.root = root

    @unwrap_to_error
    def validate_options(self, dependency: Dependency, options: PushOptions) -> Result[None]:
        """Accept native Git modes and literal branches, requiring a direct target for pins.

        Branch and patch pass; PR and overwrite fail. Direct delivery of a tag pin needs --branch.
        """
        if options.mode not in (DeliveryMode.branch, DeliveryMode.direct, DeliveryMode.patch):
            return Err(
                [
                    InvalidArguments(
                        reason=f"Git hosting does not support mode {options.mode}.",
                        ways_to_fix=["Select or configure a hosting provider that supports the requested mode."],
                    )
                ]
            )
        if options.branch is not None:
            validate_git_branch(dependency, options.branch).unwrap()
        if options.mode == DeliveryMode.direct and dependency.selector is not None:
            target_branch(dependency, options.branch, option="--branch").unwrap()
        return Ok(None)

    @unwrap_to_error
    def publish(self, request: PublicationRequest, *, source: SourceAdapter) -> Result[DeliveryResult]:
        options = request.options
        if options.mode == DeliveryMode.direct:
            return self._publish_direct(request, source=source)
        repository = options.push_repository or request.dependency.repository
        if options.mode == DeliveryMode.branch:
            validate_recipient(request, repository).unwrap()
        with self.acquire(request.dependency, revision=request.revision, refresh=False) as acquired:
            path = acquired.unwrap()
            verify_locked_content(request, path, source=source).unwrap()
            client = GitClient(path=path, repository=request.dependency.repository)
            parent = client.resolve_commit(request.revision).unwrap()
            tree = prepare_tree(client, parent, request, covering_changes(request.changes)).unwrap()
            branch = options.branch or SelectorValue("vcsinstall/" + uuid4().hex)
            if options.mode == DeliveryMode.branch and client.remote_branch(repository, branch).unwrap() is not None:
                return Err([publication_errors.BranchExists(repository=repository, branch=branch)])
            revision = client.commit_tree(tree, parent, options.message).unwrap()
            if options.mode == DeliveryMode.patch:
                assert request.patch_file is not None
                artifact_root = ProjectRootPath(request.patch_file.parent)
                artifact = FSClient(root=artifact_root, readonly=False)
                identifier = project_path_id_from_resolved(request.patch_file, artifact_root)
                artifact.write_stream(identifier, partial(client.format_patch, revision, parent)).unwrap()
                return Ok(DeliveryResult(provider=HostingProvider.git, patch_file=request.patch_file))
            publish_branch(client, repository, branch, revision, expected=None).unwrap()
            return Ok(
                DeliveryResult(
                    provider=HostingProvider.git,
                    repository=repository,
                    branch=branch,
                    revision=SourceRevision(revision),
                )
            )

    @unwrap_to_error
    def validate_artifact(self, dependency: Dependency, path: ResolvedProjectPath | None) -> Result[None]:
        """Keep patch artifacts outside the original source's native administrative storage.

        A patch next to a worktree is allowed; a patch inside its .git or a bare repository fails.
        """
        repository = dependency.repository
        if path is None or not Path(repository).is_absolute():
            return Ok(None)
        client = GitClient(path=Path(repository), repository=repository)
        common = client.common_directory().unwrap()
        if common is not None and (path.is_relative_to(common) or path == Path(repository) / ".git"):
            return Err([InvalidArguments(reason="Patch output overlaps native source administrative data.")])
        if common is not None and path.is_relative_to(Path(repository) / ".git"):
            return Err([InvalidArguments(reason="Patch output overlaps native source administrative data.")])
        return Ok(None)

    @unwrap_to_error
    def _publish_direct(self, request: PublicationRequest, *, source: SourceAdapter) -> Result[DeliveryResult]:
        options = request.options
        repository = options.push_repository or request.dependency.repository
        branch = target_branch(request.dependency, options.branch, option="--branch").unwrap()
        validate_recipient(request, repository, branch=branch).unwrap()
        recipient = request.dependency.replace(
            repository=repository, selector=VersionSelector(kind=SelectorKind.branch, value=branch)
        )
        with self.acquire(recipient) as acquired:
            path = acquired.unwrap()
            client = GitClient(path=path, repository=repository)
            parent = client.resolve_commit(f"refs/heads/{branch}").unwrap()
            resolved = ResolvedSource(dependency=recipient, path=path, revision=SourceRevision(parent))
            content = source.inspect(resolved, tuple(change.source_path for change in request.changes)).unwrap()
            pending = []
            for change in request.changes:
                destination = content[change.source_path].file_hashes
                if destination == change.content.file_hashes:
                    continue
                if destination != change.baseline:
                    return Err(
                        [
                            publication_errors.DestinationConflict(
                                repository=repository, source_path=change.source_path, revision=SourceRevision(parent)
                            )
                        ]
                    )
                pending.append(change)
            revision = parent
            if pending:
                tree = prepare_tree(client, parent, request, covering_changes(tuple(pending))).unwrap()
                revision = client.commit_tree(tree, parent, options.message).unwrap()
                publish_branch(client, repository, branch, revision, expected=parent).unwrap()
            return Ok(
                DeliveryResult(
                    provider=HostingProvider.git,
                    repository=repository,
                    branch=branch,
                    revision=SourceRevision(revision),
                )
            )

    @staticmethod
    def probe_repository(  # noqa: CCR001 — Reject known locator formats before transport access.
        repository: RepositoryLocator,
    ) -> Result[bool]:
        if not repository:
            return Ok(False)
        if urls.is_url(repository):
            try:
                url = urlsplit(repository)
            except ValueError:
                return Ok(False)
            if not url.scheme or "+" in url.scheme:
                return Ok(False)
            if url.scheme == "file" and (url.netloc not in ("", "localhost") or url.query):
                return Ok(False)
        return GitClient.probe_remote(repository)

    @contextmanager
    def acquire(
        self, dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> Iterator[Result[Path]]:
        with RepoCache(root=self.root, dependency=dependency).lock() as cached:
            if cached.is_err():
                yield Err(cached.unwrap_err())
            else:
                yield self._acquire(dependency, cached.unwrap(), revision=revision, refresh=refresh)

    @unwrap_to_error
    def _acquire(  # noqa: CCR001 — Acquire required objects and refresh version metadata only when requested.
        self, dependency: Dependency, directory: Path, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> Result[Path]:
        path = resolve_inside_project(directory / "repository.git", resolve_project_root(directory).unwrap()).unwrap()
        client = GitClient(path=path, repository=dependency.repository)
        if not path.exists():
            clone_mirror(dependency.repository, path).unwrap()
        else:
            origin = client.run(["config", "--get", "remote.origin.url"]).unwrap().decode().strip()
            if origin != dependency.repository:
                return Err(
                    [
                        errors.RepositoryAccess(
                            vcs=dependency.vcs,
                            repository=dependency.repository,
                            reason="Cached repository has a different origin.",
                        )
                    ]
                )
        selector = dependency.selector
        if refresh and selector is not None and selector.kind == SelectorKind.revision:
            if client.resolve_object_id(selector.value).unwrap() is None:
                if client.is_full_object_id(selector.value).unwrap():
                    client.run(["fetch", "origin", selector.value.lower()]).unwrap()
                else:
                    client.fetch_references().unwrap()
        elif refresh:
            client.fetch_references().unwrap()
            client.refresh_remote_head().unwrap()
        if revision is not None and client.resolve_object_id(revision).unwrap() is None:
            client.run(["fetch", "origin", revision]).unwrap()
        return Ok(path)
