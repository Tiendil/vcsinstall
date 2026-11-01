from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import resolve_project_root

from vcsinstall.domain import ContentKind, Dependency, RepositoryLocator, SourceRevision
from vcsinstall.filesystem import FSClient
from vcsinstall.sources import errors


class FilesystemHostingAdapter:
    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        return Ok(False)

    @contextmanager
    def acquire(self, dependency: Dependency, *, revision: SourceRevision | None = None) -> Iterator[Result[Path]]:
        yield self._acquire(dependency)

    @unwrap_to_error
    def _acquire(self, dependency: Dependency) -> Result[Path]:
        client = FSClient(root=resolve_project_root(Path(dependency.repository)).unwrap(), readonly=True)
        if client.inspect_kind().unwrap() != ContentKind.directory:
            return Err(
                [
                    errors.RepositoryAccess(
                        vcs=dependency.vcs,
                        repository=dependency.repository,
                        reason="Source must be an existing directory.",
                    )
                ]
            )
        return Ok(client.root)
