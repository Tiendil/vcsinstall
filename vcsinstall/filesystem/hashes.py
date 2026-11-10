"""Pure content hashing, independent of filesystem access and project state."""

import hashlib
from collections.abc import Mapping

from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash


def hash_file(content: bytes) -> ContentHash:
    """Hash exact file bytes without decoding or applying content filters."""
    return ContentHash("sha256:" + hashlib.sha256(content).hexdigest())


def hash_files(files: Mapping[ProjectPathId, ContentHash]) -> ContentHash:
    """Hash supplied canonical paths and file digests in UTF-8 byte order."""
    digest = hashlib.sha256()
    for path, file_hash in sorted(files.items(), key=lambda item: item[0].encode("utf-8")):
        digest.update(b"file\0")
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(file_hash.encode("ascii") + b"\0")
    return ContentHash("sha256:" + digest.hexdigest())
