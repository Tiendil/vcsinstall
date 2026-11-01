from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import resolve_inside_project, resolve_project_root

from vcsinstall.cache import CacheRootPath, RepoCache
from vcsinstall.domain import Dependency, RepositoryLocator, SelectorKind, urls
from vcsinstall.sources import errors
from vcsinstall.vcs_clients import GitClient, clone_mirror


class GitHostingAdapter:
    def __init__(self, *, root: CacheRootPath) -> None:
        self.root = root

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
    def acquire(self, dependency: Dependency) -> Iterator[Result[Path]]:
        with RepoCache(root=self.root, dependency=dependency).lock() as cached:
            if cached.is_err():
                yield Err(cached.unwrap_err())
            else:
                yield self._acquire(dependency, cached.unwrap())

    @unwrap_to_error
    def _acquire(self, dependency: Dependency, directory: Path) -> Result[Path]:  # noqa: CCR001
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
        if selector is None or selector.kind != SelectorKind.revision:
            client.fetch_references().unwrap()
            client.refresh_remote_head().unwrap()
        elif client.resolve_object_id(selector.value).unwrap() is None:
            if client.is_full_object_id(selector.value).unwrap():
                client.run(["fetch", "origin", selector.value.lower()]).unwrap()
            else:
                client.fetch_references().unwrap()
        return Ok(path)
