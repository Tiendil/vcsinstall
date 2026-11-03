import hashlib
import os
import re
import subprocess  # noqa: S404 — Native Git is invoked without a shell and with arguments separated.
import uuid
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, NewType

from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import resolve_project_root

from vcsinstall.domain import ContentHash, RepositoryLocator, VcsId
from vcsinstall.vcs_clients import errors

GitObjectId = NewType("GitObjectId", str)


def is_git_object_id(value: str) -> bool:
    """Check canonical full lowercase SHA-1 or SHA-256 object-ID spelling.

    Example: 'a' repeated 40 or 64 times returns True; 'ABCD1234' and 'a'
    repeated 39 times return False. Object existence is not checked.
    """
    return re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is not None


def is_git_object_id_input(value: str) -> bool:
    """Check full or abbreviated hexadecimal input before repository lookup.

    Example: 'ABCD1234' passes this syntax check; 'abc' and 'HEAD~1' fail.
    Passing does not establish that a commit exists or the prefix is unique.
    """
    return re.fullmatch(r"[0-9a-fA-F]{4,64}", value) is not None


class GitObjectKind(StrEnum):
    blob = "blob"
    tree = "tree"
    commit = "commit"
    tag = "tag"


class GitTreeEntry(BaseEntity):
    """Native tree metadata, including undecoded path bytes and original modes."""

    mode: bytes
    kind: GitObjectKind
    object_id: GitObjectId
    path: bytes


class GitCommit(BaseEntity):
    tree: GitObjectId
    parents: tuple[GitObjectId, ...]
    message: bytes


def _git_command(arguments: list[str], path: Path | None) -> list[str]:
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never"]
    if path is not None:
        command += ["-C", str(path)]
    return command + arguments


def _git_environment() -> dict[str, str]:
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")


def _command_failure(repository: RepositoryLocator, returncode: int, diagnostic: bytes) -> errors.CommandFailed:
    return errors.CommandFailed(
        vcs=VcsId.git,
        repository=repository,
        reason=diagnostic.decode("utf-8", errors="replace").strip() or f"Git exited with {returncode}.",
        returncode=returncode,
    )


def _run_git(
    arguments: list[str],
    *,
    repository: RepositoryLocator,
    path: Path | None = None,
    stdout: BinaryIO | None = None,
    timeout: float | None = None,
    input: bytes | None = None,
) -> Result[bytes]:
    try:
        result = subprocess.run(  # noqa: S603 — Git arguments are passed as data without a shell.
            _git_command(arguments, path),
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(),
            check=False,
            timeout=timeout,
            input=input,
        )  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        return Err(
            [
                errors.CommandFailed(
                    vcs=VcsId.git,
                    repository=repository,
                    reason=f"Git command timed out after {exc.timeout:g} seconds.",
                ).with_cause(exc)
            ]
        )
    except OSError as exc:
        return Err([errors.CommandFailed(vcs=VcsId.git, repository=repository, reason=str(exc)).with_cause(exc)])
    if result.returncode:
        return Err([_command_failure(repository, result.returncode, result.stderr)])
    return Ok(result.stdout or b"")


@unwrap_to_error
def clone_mirror(repository: RepositoryLocator, destination: Path) -> Result[None]:
    """Clone before constructing a client bound to the local repository."""
    _run_git(
        ["clone", "--mirror", "--no-hardlinks", "--", repository, str(destination)], repository=repository
    ).unwrap()
    return Ok(None)


class GitClient:
    """Native Git operations bound to one local repository and diagnostic locator."""

    __slots__ = ("path", "repository")

    def __init__(self, *, path: Path, repository: RepositoryLocator) -> None:
        self.path = path
        self.repository = repository

    def run(
        self, arguments: list[str], *, input: bytes | None = None, stdout: BinaryIO | None = None
    ) -> Result[bytes]:
        """Run a native command within this repository."""
        return _run_git(arguments, repository=self.repository, path=self.path, input=input, stdout=stdout)

    @staticmethod
    def probe_remote(repository: RepositoryLocator, *, timeout: float = 5.0) -> Result[bool]:
        """Confirm native remote access, including repositories without references."""
        return _run_git(["ls-remote", "--", repository, "HEAD"], repository=repository, timeout=timeout).map(
            lambda _: True
        )

    def is_repository(self) -> Result[bool]:
        """Recognize the exact worktree or bare root without searching its ancestors."""
        try:
            marker = self.path / ".git"
            if marker.exists():
                candidate = marker
            elif (self.path / "HEAD").is_file() and (self.path / "objects").is_dir():
                candidate = self.path
            else:
                return Ok(False)
        except OSError as exc:
            return Err(
                [errors.CommandFailed(vcs=VcsId.git, repository=self.repository, reason=str(exc)).with_cause(exc)]
            )
        return self.run(["rev-parse", "--resolve-git-dir", str(candidate)]).map(lambda _: True)

    def initialize(self, *, initial_branch: str, object_format: str = "sha1") -> Result[None]:
        """Initialize this local repository, creating its directory if needed."""
        return _run_git(
            ["init", f"--initial-branch={initial_branch}", f"--object-format={object_format}", "--", str(self.path)],
            repository=self.repository,
        ).map(lambda _: None)

    def set_config(self, name: str, value: str) -> Result[None]:
        """Set a repository-local Git configuration value."""
        return self.run(["config", "--local", "--", name, value]).map(lambda _: None)

    def add(self, *paths: str) -> Result[None]:
        return self.run(["add", "--", *paths]).map(lambda _: None)

    def remove(self, *paths: str) -> Result[None]:
        return self.run(["rm", "--", *paths]).map(lambda _: None)

    @unwrap_to_error
    def commit(self, message: str, *, all_tracked: bool = False) -> Result[GitObjectId]:
        arguments = ["commit", "--message", message]
        if all_tracked:
            arguments.append("--all")
        self.run(arguments).unwrap()
        return self.resolve_commit("HEAD")

    def create_branch(self, name: str, *, checkout: bool = False) -> Result[None]:
        arguments = ["checkout", "-b", name, "--"] if checkout else ["branch", "--", name]
        return self.run(arguments).map(lambda _: None)

    def create_tag(self, name: str, *, message: str | None = None) -> Result[None]:
        arguments = ["tag"]
        if message is not None:
            arguments += ["--annotate", "--message", message]
        return self.run(arguments + ["--", name]).map(lambda _: None)

    def delete_tag(self, name: str) -> Result[None]:
        return self.run(["tag", "--delete", "--", name]).map(lambda _: None)

    @unwrap_to_error
    def resolve_object_id(self, value: str) -> Result[GitObjectId | None]:
        """Resolve a hexadecimal ID or prefix locally, returning None when absent."""
        matches = self.run(["rev-parse", f"--disambiguate={value.lower()}"]).unwrap().splitlines()
        if len(matches) > 1:
            return Err([errors.ObjectIdResolution(repository=self.repository, value=value, matches=len(matches))])
        return Ok(GitObjectId(matches[0].decode("ascii")) if matches else None)

    @unwrap_to_error
    def is_full_object_id(self, value: str) -> Result[bool]:
        """Check full hexadecimal spelling against this repository's storage format.

        Example: 'a' repeated 40 times passes for SHA-1 storage but fails for
        SHA-256 storage, which needs 64 hexadecimal characters. This queries
        the repository format without proving the named object exists.
        """
        object_format = self.run(["rev-parse", "--show-object-format"]).unwrap().strip()
        length = {b"sha1": 40, b"sha256": 64}.get(object_format)
        if length is None:
            return Err(
                [
                    errors.CommandFailed(
                        vcs=VcsId.git,
                        repository=self.repository,
                        reason=f"Unsupported Git object format: {object_format!r}.",
                    )
                ]
            )
        return Ok(len(value) == length and is_git_object_id_input(value))

    def fetch_references(self) -> Result[None]:
        """Fetch current branch and tag objects into the mirrored namespaces."""
        return self.run(
            ["fetch", "--force", "--prune", "origin", "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"]
        ).map(lambda _: None)

    @unwrap_to_error
    def resolve_object(self, value: str) -> Result[GitObjectId]:
        """Resolve hexadecimal object IDs directly and other native revision expressions."""
        if is_git_object_id_input(value):
            identifier = self.resolve_object_id(value).unwrap()
            if identifier is None:
                return Err([errors.ObjectIdResolution(repository=self.repository, value=value, matches=0)])
            return Ok(identifier)
        return self.run(["rev-parse", "--verify", "--end-of-options", value]).map(
            lambda output: GitObjectId(output.decode("ascii").strip())
        )

    @unwrap_to_error
    def resolve_commit(self, value: str, *, peel: bool = False) -> Result[GitObjectId]:
        revision = self.resolve_object(value + ("^{commit}" if peel else "")).unwrap()
        kind = self.run(["cat-file", "-t", revision]).unwrap()
        if kind != b"commit\n":
            return Err([errors.InvalidCommit(repository=self.repository, value=value)])
        return Ok(revision)

    def reference_exists(self, reference: str) -> Result[bool]:
        """Distinguish an absent exact ref from a failed Git invocation."""
        result = self.run(["show-ref", "--verify", "--quiet", "--", reference])
        if result.is_ok():
            return Ok(True)
        problems = result.unwrap_err()
        if any(not isinstance(problem, errors.CommandFailed) or problem.returncode != 1 for problem in problems):
            return Err(problems)
        return Ok(False)

    def symbolic_reference(self, reference: str) -> Result[str | None]:
        """Read one symbolic reference, distinguishing absent or direct refs from failures."""
        result = self.run(["symbolic-ref", "--quiet", "--no-recurse", "--", reference])
        if result.is_err():
            problems = result.unwrap_err()
            if all(isinstance(problem, errors.CommandFailed) and problem.returncode == 1 for problem in problems):
                return Ok(None)
            return result.map(lambda _: None)
        try:
            return Ok(result.unwrap().decode("utf-8").removesuffix("\n"))
        except UnicodeDecodeError as exc:
            return Err(
                [errors.CommandFailed(vcs=VcsId.git, repository=self.repository, reason=str(exc)).with_cause(exc)]
            )

    def set_symbolic_reference(self, reference: str, target: str | None) -> Result[None]:
        """Set a symbolic reference, or remove the reference itself without following it."""
        if target is None:
            return self.run(["update-ref", "--no-deref", "-d", "--", reference]).map(lambda _: None)
        return self.run(["symbolic-ref", "--", reference, target]).map(lambda _: None)

    @unwrap_to_error
    def refresh_remote_head(self) -> Result[None]:
        """Retain the source's advertised symbolic HEAD in native remote-tracking metadata."""
        metadata = self.run(["ls-remote", "--symref", "--", self.repository, "HEAD"]).unwrap()
        targets = {
            line.removeprefix(b"ref: ").removesuffix(b"\tHEAD")
            for line in metadata.splitlines()
            if line.startswith(b"ref: ") and line.endswith(b"\tHEAD")
        }
        try:
            target = next(iter(targets)).decode("utf-8") if len(targets) == 1 else None
        except UnicodeDecodeError as exc:
            return Err(
                [errors.CommandFailed(vcs=VcsId.git, repository=self.repository, reason=str(exc)).with_cause(exc)]
            )
        return self.set_symbolic_reference("refs/remotes/origin/HEAD", target)

    @unwrap_to_error
    def list_tree(self, revision: str) -> Result[tuple[GitTreeEntry, ...]]:
        listing = self.run(["ls-tree", "-r", "-t", "-z", "--full-tree", revision]).unwrap()
        entries = []
        for record in listing.split(b"\0"):
            if not record:
                continue
            metadata, name = record.split(b"\t", 1)
            mode, object_kind, object_id = metadata.split(b" ")
            entries.append(
                GitTreeEntry(
                    mode=mode,
                    kind=GitObjectKind(object_kind.decode("ascii")),
                    object_id=GitObjectId(object_id.decode("ascii")),
                    path=name,
                )
            )
        return Ok(tuple(entries))

    def hash_blob(self, object_id: GitObjectId) -> Result[ContentHash]:
        """Hash committed bytes without retaining the blob in Python memory."""
        try:
            with subprocess.Popen(  # noqa: S603 — Native Git arguments are passed without a shell.
                _git_command(["cat-file", "blob", object_id], self.path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_git_environment(),
            ) as process:
                assert process.stdout is not None
                # Binary buffered subprocess pipes support readinto, omitted by IO[bytes].
                digest = hashlib.file_digest(process.stdout, "sha256")  # type: ignore[arg-type]
                _, diagnostic = process.communicate()
                if process.returncode:
                    return Err([_command_failure(self.repository, process.returncode, diagnostic)])
        except OSError as exc:
            return Err(
                [errors.CommandFailed(vcs=VcsId.git, repository=self.repository, reason=str(exc)).with_cause(exc)]
            )
        return Ok(ContentHash("sha256:" + digest.hexdigest()))

    def copy_blob(self, object_id: GitObjectId, destination: BinaryIO) -> Result[None]:
        """Send exact committed bytes directly to a caller-owned file."""
        return _run_git(
            ["cat-file", "blob", object_id], repository=self.repository, path=self.path, stdout=destination
        ).map(lambda _: None)

    def copy_file(self, revision: GitObjectId, path: str, destination: BinaryIO) -> Result[None]:
        """Send a literal file's committed bytes directly to a caller-owned file."""
        return _run_git(
            ["cat-file", "blob", f"{revision}:{path}"],
            repository=self.repository,
            path=self.path,
            stdout=destination,
        ).map(lambda _: None)

    @unwrap_to_error
    def remote_branch(self, repository: RepositoryLocator, branch: str) -> Result[GitObjectId | None]:
        """Inspect one literal branch in the explicit recipient, independently of cached refs."""
        reference = f"refs/heads/{branch}".encode("utf-8")
        listing = self.run(["ls-remote", "--refs", "--", repository, reference.decode("utf-8")]).unwrap()
        identifiers = [line.split(b"\t")[0] for line in listing.splitlines() if line.endswith(b"\t" + reference)]
        return Ok(GitObjectId(identifiers[0].decode("ascii")) if len(identifiers) == 1 else None)

    def fetch_branch(self, repository: RepositoryLocator, branch: str) -> Result[None]:
        """Retrieve complete branch history without replacing source reference observations."""
        return self.run(["fetch", "--no-tags", "--no-write-fetch-head", "--", repository, f"refs/heads/{branch}"]).map(
            lambda _: None
        )

    def store_file(self, path: Path) -> Result[GitObjectId]:
        """Stream a previously inspected local file into a blob without Git filters."""
        return self.run(["hash-object", "-w", "--no-filters", "--", str(path)]).map(
            lambda value: GitObjectId(value.decode("ascii").strip())
        )

    @unwrap_to_error
    def write_tree(self, entries: tuple[GitTreeEntry, ...]) -> Result[GitObjectId]:
        """Build native trees from literal path bytes, preserving modes and unselected objects."""
        direct: dict[bytes, GitTreeEntry] = {}
        children: dict[bytes, list[GitTreeEntry]] = {}
        for entry in entries:
            name, separator, remainder = entry.path.partition(b"/")
            if separator:
                children.setdefault(name, []).append(entry.replace(path=remainder))
            else:
                direct[name] = entry
        for name, descendants in children.items():
            direct[name] = GitTreeEntry(
                mode=b"040000",
                kind=GitObjectKind.tree,
                object_id=self.write_tree(tuple(descendants)).unwrap(),
                path=name,
            )
        records = b"".join(
            entry.mode
            + b" "
            + entry.kind.encode("ascii")
            + b" "
            + entry.object_id.encode("ascii")
            + b"\t"
            + name
            + b"\0"
            for name, entry in sorted(direct.items())
        )
        return self.run(["mktree", "-z", "--missing"], input=records).map(
            lambda value: GitObjectId(value.decode("ascii").strip())
        )

    @unwrap_to_error
    def commit_tree(self, tree: GitObjectId, parent: GitObjectId, message: str) -> Result[GitObjectId]:
        """Create one commit with native identity and signing configuration, without touching refs."""
        signing = self.run(["config", "--type=bool", "--get", "commit.gpgsign"])
        arguments = ["commit-tree", tree, "-p", parent, "-F", "-"]
        if signing.is_ok() and signing.unwrap().strip() == b"true":
            arguments.append("-S")
        elif signing.is_err() and any(
            not isinstance(problem, errors.CommandFailed) or problem.returncode != 1
            for problem in signing.unwrap_err()
        ):
            return Err(signing.unwrap_err())
        payload = message.encode("utf-8")
        if not payload.endswith(b"\n"):
            payload += b"\n"
        return self.run(arguments, input=payload).map(lambda value: GitObjectId(value.decode("ascii").strip()))

    @unwrap_to_error
    def inspect_commit(self, revision: GitObjectId) -> Result[GitCommit]:
        value = self.run(["cat-file", "commit", revision]).unwrap()
        headers, _, message = value.partition(b"\n\n")
        return Ok(
            GitCommit(
                tree=GitObjectId(
                    next(line[5:] for line in headers.splitlines() if line.startswith(b"tree ")).decode("ascii")
                ),
                parents=tuple(
                    GitObjectId(line[7:].decode("ascii"))
                    for line in headers.splitlines()
                    if line.startswith(b"parent ")
                ),
                message=message,
            )
        )

    def is_ancestor(self, ancestor: GitObjectId, descendant: GitObjectId) -> Result[bool]:
        """Check ancestry using available complete history; a normal non-ancestor returns False.

        For example, A is an ancestor of its child B, while sibling commits are not ancestors.
        Missing objects remain native-client failures.
        """
        result = self.run(["merge-base", "--is-ancestor", ancestor, descendant])
        if result.is_ok():
            return Ok(True)
        problems = result.unwrap_err()
        if all(isinstance(problem, errors.CommandFailed) and problem.returncode == 1 for problem in problems):
            return Ok(False)
        return Err(problems)

    def push_branch(
        self, repository: RepositoryLocator, branch: str, revision: GitObjectId, *, expected: GitObjectId | None
    ) -> Result[None]:
        """Publish exactly one ref to one explicit URL with a conditional expected recipient state."""
        remote = "vcsinstall-" + uuid.uuid4().hex
        reference = f"refs/heads/{branch}"
        return self.run(
            [
                "-c",
                f"remote.{remote}.url={repository}",
                "-c",
                f"remote.{remote}.pushurl={repository}",
                "-c",
                f"remote.{remote}.mirror=false",
                "push",
                "--porcelain",
                "--no-mirror",
                "--no-follow-tags",
                "--recurse-submodules=no",
                f"--force-with-lease={reference}:{expected or ''}",
                "--",
                remote,
                f"{revision}:{reference}",
            ]
        ).map(lambda _: None)

    def format_patch(self, revision: GitObjectId, base: GitObjectId, output: BinaryIO) -> Result[None]:
        return self.run(
            [
                "format-patch",
                "--stdout",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                f"--base={base}",
                "-1",
                revision,
                "--",
            ],
            stdout=output,
        ).map(lambda _: None)

    @unwrap_to_error
    def common_directory(self) -> Result[Path | None]:
        """Find an enclosing Git repository, including linked worktrees, without mutating it."""
        try:
            if not any((parent / ".git").exists() for parent in (self.path, *self.path.parents)) and not (
                (self.path / "HEAD").is_file() and (self.path / "objects").is_dir()
            ):
                return Ok(None)
        except OSError as exc:
            return Err(
                [errors.CommandFailed(vcs=VcsId.git, repository=self.repository, reason=str(exc)).with_cause(exc)]
            )
        directory = self.run(["rev-parse", "--path-format=absolute", "--git-common-dir"]).unwrap()
        return Ok(resolve_project_root(Path(os.fsdecode(directory.rstrip(b"\n")))).unwrap())

    @unwrap_to_error
    def checked_out_branches(self) -> Result[tuple[str, ...]]:
        listing = self.run(["worktree", "list", "--porcelain", "-z"]).unwrap()
        return Ok(tuple(os.fsdecode(field[7:]) for field in listing.split(b"\0") if field.startswith(b"branch ")))
