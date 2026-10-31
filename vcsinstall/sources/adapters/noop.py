from pathlib import Path
from typing import BinaryIO

from llm_tool_cli.core.result import Err, Ok, Result

from vcsinstall.domain import Dependency, RepositoryLocator, SelectorKind, SourcePath, SourceRevision, VcsId
from vcsinstall.filesystem import ContentTree
from vcsinstall.sources import errors
from vcsinstall.sources.entities import ResolvedSource


class NoopSourceAdapter:
    """Validate opaque source identifiers without consulting fixture state."""

    vcs = VcsId.noop

    def normalize_repository(self, repository: RepositoryLocator, *, cwd: Path) -> Result[RepositoryLocator | None]:
        return Ok(repository if repository else None)

    def probe_repository(self, repository: RepositoryLocator, *, cwd: Path) -> Result[bool]:
        return Ok(False)

    def resolve(self, dependency: Dependency, path: Path) -> Result[ResolvedSource]:
        return Err(
            [
                errors.RepositoryAccess(
                    vcs=VcsId.noop,
                    repository=dependency.repository,
                    reason="No controlled source fixture was supplied.",
                )
            ]
        )

    def inspect(self, source: ResolvedSource, paths: tuple[SourcePath, ...]) -> Result[dict[SourcePath, ContentTree]]:
        return Err(
            [
                errors.RepositoryAccess(
                    vcs=VcsId.noop,
                    repository=source.dependency.repository,
                    reason="No controlled source fixture was supplied.",
                )
            ]
        )

    def copy_file(
        self,
        source: ResolvedSource,
        source_path: SourcePath,
        output: BinaryIO,
    ) -> Result[None]:
        return Err(
            [
                errors.RepositoryAccess(
                    vcs=VcsId.noop,
                    repository=source.dependency.repository,
                    reason="No controlled source fixture was supplied.",
                )
            ]
        )

    def validate_input(self, dependency: Dependency) -> Result[None]:
        return Ok(None)

    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        if dependency.selector is not None and dependency.selector.kind == SelectorKind.unresolved:
            return Err([errors.UnsupportedSelector(vcs=VcsId.noop, field=dependency.selector.kind.value)])
        return Ok(None)

    def validate_revision_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        validation = self.validate_dependency(dependency)
        if validation.is_err():
            return validation
        if not revision:
            return Err([errors.InvalidRevision(vcs=VcsId.noop, field="revision", value=revision)])
        selector = dependency.selector
        if selector is not None and selector.kind == SelectorKind.revision:
            # The requested pin and locked revision use the same opaque representation.
            if selector.value != revision:  # type: ignore[comparison-overlap]
                return Err([errors.RevisionMismatch(revision=revision, expected=selector.value)])
        return Ok(None)
