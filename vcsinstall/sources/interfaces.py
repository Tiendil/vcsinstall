from pathlib import Path
from typing import BinaryIO, Protocol

from llm_tool_cli.core.result import Result

from vcsinstall.domain import Dependency, RepositoryLocator, SourcePath, SourceRevision, VcsId, VersionSelector
from vcsinstall.filesystem import ContentTree
from vcsinstall.sources.entities import ResolvedSource


class SourceAdapter(Protocol):
    """Backend validation, version resolution, and local repository inspection."""

    vcs: VcsId

    def selectors_match(self, declared: VersionSelector | None, recorded: VersionSelector | None) -> bool:
        """Compare persisted selectors, including retained backend defaults, without I/O.

        Example: Git may match an omitted declaration to a recorded branch
        'main', but must not treat a recorded tag 'main' as that default branch.
        """
        ...

    def normalize_repository(self, repository: RepositoryLocator, *, cwd: Path) -> Result[RepositoryLocator | None]:
        """Normalize a locator, returning None for malformed or unsupported input."""
        ...

    def probe_repository(self, repository: RepositoryLocator, *, cwd: Path) -> Result[bool]:
        """Recognize backend notation or local metadata, retaining operational failures."""
        ...

    def validate_dependency(self, dependency: Dependency) -> Result[None]:
        """Validate persisted declaration fields without external access.

        Example: a persisted Git revision pin 'abcd1234' fails because stored
        pins require a complete lowercase commit identifier, unlike CLI input.
        """
        ...

    def validate_input(self, dependency: Dependency) -> Result[None]:
        """Validate supported input forms without I/O; reject input with InvalidArguments.

        Example: Git accepts revision input 'abcd1234' for later resolution,
        but rejects 'HEAD~1' as a revision pin. Existence is checked later.
        """
        ...

    def validate_revision_lock(self, dependency: Dependency, revision: SourceRevision) -> Result[None]:
        """Validate a dependency revision lock without external access.

        Example: a Git pin to '1' repeated 40 times cannot lock '2' repeated 40
        times. A tracking branch may record a syntactically valid revision
        without checking its current upstream target here.
        """
        ...

    def resolve(self, dependency: Dependency, path: Path) -> Result[ResolvedSource]:
        """Resolve validated input in an acquired repository, returning a persistable declaration."""
        ...

    def inspect(self, source: ResolvedSource, paths: tuple[SourcePath, ...]) -> Result[dict[SourcePath, ContentTree]]:
        """Inspect selected content at one revision and return plain metadata."""
        ...

    def copy_file(
        self,
        source: ResolvedSource,
        source_path: SourcePath,
        output: BinaryIO,
    ) -> Result[None]:
        """Stream a regular file identified by inspect at the selected revision.

        The caller must account for its own intervening source changes before
        reusing that inspection.
        """
        ...
