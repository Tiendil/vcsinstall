import re
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import (
    ContentHash,
    ContentKind,
    Dependency,
    RepositoryLocator,
    SelectorKind,
    SelectorValue,
    SourcePath,
    SourceRevision,
    VcsId,
    VersionSelector,
    urls,
)
from vcsinstall.filesystem import ContentTree, FileInfo
from vcsinstall.sources import errors
from vcsinstall.sources.entities import ResolvedSource
from vcsinstall.sources.locators import normalize_local_repository
from vcsinstall.vcs_clients import (
    GitClient,
    GitObjectId,
    GitObjectKind,
    GitTreeEntry,
    is_git_object_id,
    is_git_object_id_input,
)


def _is_scp_address(repository: RepositoryLocator) -> bool:
    return not urls.is_url(repository) and re.match(r"(?:[^/]+@)?[^/:]+:.+", repository) is not None


def _validate_reference(value: SelectorValue, *, branch: bool) -> Result[None]:
    """Check native ref spelling without requiring Git or repository access."""
    components = value.split("/")
    if (
        value.endswith(".")
        or ".." in value
        or "@{" in value
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
        or any(not part or part.startswith(".") or part.endswith(".lock") for part in components)
        or (branch and (value.startswith("-") or value == "HEAD"))
    ):
        return Err([errors.InvalidGitReference(field="branch" if branch else "tag", value=value)])
    return Ok(None)


def _validate_revision(value: str, *, field: str) -> Result[None]:
    if not is_git_object_id(value):
        return Err([errors.InvalidRevision(vcs=VcsId.git, field=field, value=value)])
    return Ok(None)


@unwrap_to_error
def _infer_reference(client: GitClient, reference: SelectorValue) -> Result[VersionSelector]:
    candidates = []
    for kind, namespace in ((SelectorKind.branch, "heads"), (SelectorKind.tag, "tags")):
        if client.reference_exists(f"refs/{namespace}/{reference}").unwrap():
            candidates.append(VersionSelector(kind=kind, value=reference))
    if is_git_object_id_input(reference):
        verified = client.resolve_commit(reference)
        if verified.is_ok():
            candidates.append(VersionSelector(kind=SelectorKind.revision, value=SelectorValue(verified.unwrap())))
    if len(candidates) != 1:
        return Err(
            [
                InvalidArguments(
                    reason=f"Reference {reference!r} is missing or ambiguous; "
                    "use a typed #branch=, #tag=, or #rev= selector."
                )
            ]
        )
    return Ok(candidates[0])


def _select_tree(
    entries: tuple[GitTreeEntry, ...], path: SourcePath, *, repository: RepositoryLocator
) -> Result[tuple[ContentKind, tuple[GitTreeEntry, ...]]]:  # noqa: TAE002 — The selected kind and entries stay local.
    prefix = path.removeprefix("@/").encode("utf-8")
    selected = tuple(
        entry for entry in entries if not prefix or entry.path == prefix or entry.path.startswith(prefix + b"/")
    )
    unsupported = next((entry for entry in selected if entry.mode not in (b"100644", b"100755", b"040000")), None)
    if unsupported is not None:
        return Err(
            [
                errors.RepositoryAccess(
                    vcs=VcsId.git,
                    repository=repository,
                    reason=f"Unsupported Git object at {unsupported.path!r}.",
                )
            ]
        )
    exact = next((entry for entry in selected if entry.path == prefix), None)
    kind = ContentKind.directory if not prefix else ContentKind.absent
    if exact is not None:
        kind = ContentKind.directory if exact.kind == GitObjectKind.tree else ContentKind.file
    files = tuple(entry for entry in selected if entry.kind != GitObjectKind.tree)
    return Ok((kind, files))


def _file_info(
    entry: GitTreeEntry, path: SourcePath, digest: ContentHash, *, repository: RepositoryLocator
) -> Result[FileInfo]:
    prefix = path.removeprefix("@/").encode("utf-8")
    relative = entry.path[len(prefix) :].lstrip(b"/") if prefix else entry.path
    try:
        identifier = ProjectPathId("@/" + relative.decode("utf-8"))
    except UnicodeDecodeError as exc:
        return Err(
            [
                errors.RepositoryAccess(
                    vcs=VcsId.git, repository=repository, reason="Source names must be UTF-8."
                ).with_cause(exc)
            ]
        )
    return Ok(FileInfo(path=identifier, hash=digest, executable=entry.mode == b"100755"))


class GitSourceAdapter:
    vcs = VcsId.git

    @unwrap_to_error
    def normalize_repository(  # noqa: CCR001 — Distinguish rejected URL formats, native addresses, and local paths.
        self, repository: RepositoryLocator, *, cwd: Path
    ) -> Result[RepositoryLocator | None]:
        if not repository:
            return Ok(None)
        if urls.is_url(repository):
            try:
                url = urlsplit(repository)
            except ValueError:
                return Ok(None)
            if not url.scheme or ("+" in url.scheme and not url.scheme.startswith("git+")):
                return Ok(None)
            url = url._replace(scheme=url.scheme.removeprefix("git+"), fragment="")
            repository = RepositoryLocator(url.geturl())
            if url.scheme != "file":
                return Ok(repository)
        elif _is_scp_address(repository):
            return Ok(repository)
        return normalize_local_repository(repository, cwd=cwd)

    @unwrap_to_error
    def probe_repository(self, repository: RepositoryLocator, *, cwd: Path) -> Result[bool]:
        native = self.normalize_repository(repository, cwd=cwd).unwrap()
        if native is None:
            return Ok(False)
        if urls.is_url(repository):
            return Ok(repository.lower().startswith(("git+", "git://")))
        if _is_scp_address(native):
            return Ok(True)
        return GitClient(path=Path(native), repository=native).is_repository()

    @unwrap_to_error
    def resolve(self, dependency: Dependency, path: Path) -> Result[ResolvedSource]:  # noqa: CCR001
        client = GitClient(path=path, repository=dependency.repository)
        selector = dependency.selector
        if selector is not None and selector.kind == SelectorKind.unresolved:
            selector = _infer_reference(client, selector.value).unwrap()
        if selector is None:
            head = client.symbolic_reference("refs/remotes/origin/HEAD").unwrap()
            if head is None or not head.startswith("refs/heads/"):
                return Err(
                    [
                        errors.RepositoryAccess(
                            vcs=dependency.vcs,
                            repository=dependency.repository,
                            reason="The advertised default branch is unavailable or ambiguous.",
                        )
                    ]
                )
            selector = VersionSelector(kind=SelectorKind.branch, value=SelectorValue(head.removeprefix("refs/heads/")))
        dependency = dependency.replace(selector=selector)
        if selector.kind == SelectorKind.revision:
            revision = client.resolve_commit(selector.value).unwrap()
            dependency = dependency.replace(selector=selector.replace(value=SelectorValue(revision)))
        else:
            self.validate_dependency(dependency).unwrap()
            namespace = "heads" if selector.kind == SelectorKind.branch else "tags"
            revision = client.resolve_commit(f"refs/{namespace}/{selector.value}", peel=True).unwrap()
        return Ok(ResolvedSource(dependency=dependency, path=path, revision=SourceRevision(revision)))

    @unwrap_to_error
    def inspect(self, source: ResolvedSource, paths: tuple[SourcePath, ...]) -> Result[dict[SourcePath, ContentTree]]:
        client = GitClient(path=source.path, repository=source.dependency.repository)
        entries = client.list_tree(source.revision).unwrap()
        digests: dict[GitObjectId, ContentHash] = {}
        trees: dict[SourcePath, ContentTree] = {}
        for path in dict.fromkeys(paths):
            kind, selected = _select_tree(entries, path, repository=source.dependency.repository).unwrap()
            files = []
            for entry in selected:
                if entry.object_id not in digests:
                    digests[entry.object_id] = client.hash_blob(entry.object_id).unwrap()
                file = _file_info(
                    entry, path, digests[entry.object_id], repository=source.dependency.repository
                ).unwrap()
                files.append(file)
            trees[path] = ContentTree(kind=kind, files=tuple(files))
        return Ok(trees)

    @unwrap_to_error
    def copy_file(
        self,
        source: ResolvedSource,
        source_path: SourcePath,
        output: BinaryIO,
    ) -> Result[None]:
        """Copy previously inspected bytes, bound by the immutable commit and literal path."""
        _validate_revision(source.revision, field="revision").unwrap()
        client = GitClient(path=source.path, repository=source.dependency.repository)
        return client.copy_file(GitObjectId(source.revision), source_path.removeprefix("@/"), output)

    def validate_input(self, dependency: Dependency) -> Result[None]:
        selector = dependency.selector
        if selector is None or selector.kind == SelectorKind.unresolved:
            return Ok(None)
        if selector.kind == SelectorKind.revision:
            if not is_git_object_id_input(selector.value):
                return Err(
                    [
                        InvalidArguments(
                            reason="A Git revision pin must be a complete commit identifier "
                            "or unambiguous hexadecimal abbreviation."
                        )
                    ]
                )
            return Ok(None)
        return self.validate_dependency(dependency).map_err(
            lambda problems: [InvalidArguments(reason=problem.format_message()) for problem in problems]
        )

    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        selector = dependency.selector
        if selector is None:
            return Ok(None)
        match selector.kind:
            case SelectorKind.revision:
                return _validate_revision(selector.value, field="rev")
            case SelectorKind.branch | SelectorKind.tag:
                return _validate_reference(selector.value, branch=selector.kind == SelectorKind.branch)
            case SelectorKind.unresolved:
                return Err([errors.UnsupportedSelector(vcs=VcsId.git, field=selector.kind.value)])

    @unwrap_to_error
    def validate_revision_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        self.validate_dependency(dependency).unwrap()
        _validate_revision(revision, field="revision").unwrap()
        selector = dependency.selector
        if selector is None:
            return Err([errors.MissingGitSelector()])
        if selector.kind == SelectorKind.revision:
            # Both values have passed complete Git revision validation.
            if selector.value != revision:  # type: ignore[comparison-overlap]
                return Err([errors.RevisionMismatch(revision=revision, expected=selector.value)])
        return Ok(None)
