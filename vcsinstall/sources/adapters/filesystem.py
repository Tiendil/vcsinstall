import re
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import ProjectPathId, resolve_project_root

from vcsinstall.domain import (
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
from vcsinstall.filesystem import ContentTree, FSClient, hashes
from vcsinstall.sources import errors
from vcsinstall.sources.entities import ResolvedSource
from vcsinstall.sources.locators import normalize_local_repository


def _validate_revision(value: str, *, field: str) -> Result[None]:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        return Err([errors.InvalidRevision(vcs=VcsId.filesystem, field=field, value=value)])
    return Ok(None)


@unwrap_to_error
def _inspect_source(dependency: Dependency, path: Path) -> Result[ContentTree]:
    client = FSClient(root=resolve_project_root(path).unwrap(), readonly=True)
    tree = client.inspect_tree().unwrap()
    if tree.kind != ContentKind.directory:
        return Err(
            [
                errors.RepositoryAccess(
                    vcs=dependency.vcs,
                    repository=dependency.repository,
                    reason="The source directory is no longer available.",
                )
            ]
        )
    return Ok(tree)


class FilesystemSourceAdapter:
    vcs = VcsId.filesystem

    def normalize_repository(self, repository: RepositoryLocator, *, cwd: Path) -> Result[RepositoryLocator | None]:
        if not repository:
            return Ok(None)
        if urls.is_url(repository):
            try:
                url = urlsplit(repository)
            except ValueError:
                return Ok(None)
            scheme = url.scheme.removeprefix("filesystem+")
            if scheme != "file":
                return Ok(None)
            repository = RepositoryLocator(url._replace(scheme=scheme, fragment="").geturl())
        return normalize_local_repository(repository, cwd=cwd)

    @unwrap_to_error
    def probe_repository(self, repository: RepositoryLocator, *, cwd: Path) -> Result[bool]:
        native = self.normalize_repository(repository, cwd=cwd).unwrap()
        if native is None:
            return Ok(False)
        if urls.is_url(repository):
            return Ok(True)
        client = FSClient(root=resolve_project_root(Path(native)).unwrap(), readonly=True)
        return client.inspect_kind().map(lambda kind: kind == ContentKind.directory)

    @unwrap_to_error
    def resolve(self, dependency: Dependency, path: Path) -> Result[ResolvedSource]:
        selector = dependency.selector
        if selector is not None and selector.kind == SelectorKind.unresolved:
            selector = VersionSelector(kind=SelectorKind.revision, value=selector.value)
            dependency = dependency.replace(selector=selector)
        validation = self.validate_dependency(dependency)
        if validation.is_err():
            return Err([InvalidArguments(reason=problem.format_message()) for problem in validation.unwrap_err()])
        tree = _inspect_source(dependency, path).unwrap()
        revision = SourceRevision(hashes.hash_files(tree.file_hashes))
        if selector is not None and selector.value != revision:  # type: ignore[comparison-overlap]
            return Err([errors.RevisionMismatch(revision=revision, expected=selector.value)])
        return Ok(ResolvedSource(dependency=dependency, path=path, revision=revision))

    @unwrap_to_error
    def inspect(  # noqa: CCR001 — Select multiple subtrees from one shared source manifest.
        self, source: ResolvedSource, paths: tuple[SourcePath, ...]
    ) -> Result[dict[SourcePath, ContentTree]]:
        client = FSClient(root=resolve_project_root(source.path).unwrap(), readonly=True)
        snapshot = _inspect_source(source.dependency, source.path).unwrap()
        current = SourceRevision(hashes.hash_files(snapshot.file_hashes))
        if current != source.revision:
            return Err([errors.RevisionMismatch(revision=current, expected=SelectorValue(source.revision))])
        trees: dict[SourcePath, ContentTree] = {}
        for path in dict.fromkeys(paths):
            files = []
            kind = client.inspect_kind(path).unwrap()
            for file in snapshot.files:
                if file.path == path:
                    kind = ContentKind.file
                    relative = ProjectPathId("@/")
                elif path == "@/" or file.path.startswith(path + "/"):
                    kind = ContentKind.directory
                    suffix = file.path.removeprefix(path).lstrip("/")
                    relative = ProjectPathId("@/" + suffix)
                else:
                    continue
                files.append(file.replace(path=relative))
            trees[path] = ContentTree(kind=kind, files=tuple(files))
        return Ok(trees)

    @unwrap_to_error
    def copy_file(
        self,
        source: ResolvedSource,
        source_path: SourcePath,
        output: BinaryIO,
    ) -> Result[None]:
        """Stream a regular file previously identified by inspect."""
        _validate_revision(source.revision, field="revision").unwrap()
        client = FSClient(root=resolve_project_root(source.path).unwrap(), readonly=True)
        return client.copy_file(source_path, output)

    def validate_input(self, dependency: Dependency) -> Result[None]:
        selector = dependency.selector
        if selector is not None and selector.kind == SelectorKind.unresolved:
            dependency = dependency.replace(selector=selector.replace(kind=SelectorKind.revision))
        return self.validate_dependency(dependency).map_err(
            lambda problems: [InvalidArguments(reason=problem.format_message()) for problem in problems]
        )

    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        selector = dependency.selector
        if selector is None:
            return Ok(None)
        if selector.kind != SelectorKind.revision:
            return Err([errors.UnsupportedSelector(vcs=VcsId.filesystem, field=selector.kind.value)])
        return _validate_revision(selector.value, field="rev")

    @unwrap_to_error
    def validate_revision_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        self.validate_dependency(dependency).unwrap()
        _validate_revision(revision, field="revision").unwrap()
        selector = dependency.selector
        if selector is not None:
            # Both values have passed complete filesystem fingerprint validation.
            if selector.value != revision:  # type: ignore[comparison-overlap]
                return Err([errors.RevisionMismatch(revision=revision, expected=selector.value)])
        return Ok(None)
