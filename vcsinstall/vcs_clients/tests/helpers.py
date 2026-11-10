from pathlib import Path

from vcsinstall.domain import RepositoryLocator
from vcsinstall.vcs_clients import GitClient


def create_repository(path: Path, *, object_format: str = "sha1") -> GitClient:
    client = GitClient(path=path, repository=RepositoryLocator(str(path)))
    client.initialize(initial_branch="trunk", object_format=object_format).unwrap()
    client.set_config("user.name", "Test User").unwrap()
    client.set_config("user.email", "test@example.com").unwrap()
    (path / "guide.txt").write_text("original\n")
    client.add(".").unwrap()
    client.commit("Initial source").unwrap()
    return client
