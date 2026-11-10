import json
from pathlib import Path

import tomli_w
from llm_tool_cli.config import load_config
from llm_tool_cli.paths import CacheRootPath, PathSegment, ProjectPathId
from llm_tool_cli.tool_cache import CacheIdentity
from typer.testing import CliRunner

from vcsinstall.cli.application import app
from vcsinstall.filesystem import hashes
from vcsinstall.hosting import repository_cache
from vcsinstall.workspace import Config, DirectoryBaseline, Entry, FileBaseline, InstallationBaseline, Lockfile

type CommandResult = tuple[int, list[dict[str, object]], str]


def cached_repository(root: Path) -> Path:
    """Populate owned repository data for cache-clean command tests."""
    identity = CacheIdentity(namespace=PathSegment("git"), key="https://host/shared")
    with repository_cache(CacheRootPath(root)).entry(identity) as result:
        entry = result.unwrap()
        payload = entry / "repository" / "versions"
        payload.mkdir(parents=True)
        (payload / "first").write_bytes(b"cached first revision")
        (payload / "second").write_bytes(b"cached second revision")
    return entry


def invoke_dependency_sync_to(config: Path, cache: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(
        app,
        ["-p", "automation", "--config", str(config), "--cache-dir", str(cache), "dependency", "sync-to", *arguments],
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code, [json.loads(line) for line in result.stdout.splitlines()], result.stdout


def invoke_add(config: Path, cache: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(
        app, ["-p", "automation", "--config", str(config), "--cache-dir", str(cache), "dependency", "add", *arguments]
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def invoke_entry_add(config: Path, cache: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(
        app, ["-p", "automation", "--config", str(config), "--cache-dir", str(cache), "add", *arguments]
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def invoke_entry_remove(config: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(app, ["-p", "automation", "--config", str(config), "remove", *arguments])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def invoke_entry_list(config: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(app, ["-p", "automation", "--config", str(config), "list", *arguments])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def invoke_install(config: Path, cache: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(
        app, ["-p", "automation", "--config", str(config), "--cache-dir", str(cache), "install", *arguments]
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def invoke_dependency_remove(config: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(app, ["-p", "automation", "--config", str(config), "dependency", "remove", *arguments])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def invoke_dependency_list(config: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(app, ["-p", "automation", "--config", str(config), "dependency", "list", *arguments])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def invoke_dependency_outdated(config: Path, cache: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(
        app,
        ["-p", "automation", "--config", str(config), "--cache-dir", str(cache), "dependency", "outdated", *arguments],
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def invoke_dependency_update(config: Path, cache: Path, *arguments: str) -> CommandResult:
    result = CliRunner().invoke(
        app,
        ["-p", "automation", "--config", str(config), "--cache-dir", str(cache), "dependency", "update", *arguments],
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result.exit_code, records, result.stdout


def declare_entry(config: Path, *, source: str, destination: str, files: dict[str, bytes] | bytes) -> None:
    mapping = {"dependency": "ci", "source_path": source, "destination": destination}
    configuration = load_config(config, Config).unwrap()
    configuration = configuration.replace(entries=(*configuration.entries, Entry.model_validate(mapping)))
    config.write_bytes(tomli_w.dumps(configuration.model_dump(mode="json", exclude_none=True)).encode("utf-8"))

    baseline: InstallationBaseline
    if isinstance(files, bytes):
        baseline = FileBaseline.model_validate({**mapping, "kind": "file", "hash": hashes.hash_file(files)})
    else:
        file_hashes = {ProjectPathId("@/" + path): hashes.hash_file(data) for path, data in files.items()}
        baseline = DirectoryBaseline.model_validate(
            {
                **mapping,
                "kind": "directory",
                "hash": hashes.hash_files(file_hashes),
                "files": [{"path": path, "hash": digest} for path, digest in file_hashes.items()],
            }
        )
    lock_path = config.with_name("vcsinstall.lock")
    locked = load_config(lock_path, Lockfile).unwrap()
    locked = locked.replace(entries=(*locked.entries, baseline))
    lock_path.write_bytes(tomli_w.dumps(locked.model_dump(mode="json", exclude_none=True)).encode("utf-8"))
