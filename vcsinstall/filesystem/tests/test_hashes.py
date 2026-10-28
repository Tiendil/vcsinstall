from itertools import permutations
from types import MappingProxyType

import pytest
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash
from vcsinstall.filesystem.hashes import hash_file, hash_files
from vcsinstall.workspace import AbsentContent, DirectoryContent, FileContent

_EMPTY_HASH = ContentHash("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
_GUIDE_HASH = ContentHash("sha256:5ba1677a4833fd02ece676817a073abef834d542a27562e9ad1c903b656d37dd")
_DIRECTORY_HASH = ContentHash("sha256:bea5475d87735beab89c88a39fb417625013c60ddf696d3dba4eb11d343e27fe")


class TestHashFile:
    @pytest.mark.parametrize(
        "content,expected_hash",
        [
            (b"", _EMPTY_HASH),
            (b"abc", "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
            (b"\x00\xff\x80\r\n", "sha256:6171db06a1c89b1ff8ab77e479d5df976ccd88a7b63567b4b18f413649e12ff3"),
            (b"line\r\n", "sha256:893e89e669b5a4f9e5136d565f51e341a0c5e5531816c9c1a806d90df66a45f4"),
            (b"line\n", "sha256:c73b73af8851e9e91bc6b4dc12e7dace0a2bfb931c1d0b8b36ef367319f58cd1"),
            (b"Shared license.\n", "sha256:4745da00e984a8aab4faeb69b987318e1aabdd8fad0df4a22f2a89a865299cc1"),
            (b"Shared guide.\n", _GUIDE_HASH),
        ],
    )
    def test_exact_bytes(self, content: bytes, expected_hash: str) -> None:
        assert hash_file(content) == expected_hash


class TestHashFiles:
    def test_empty_manifest(self) -> None:
        assert hash_files({}) == _EMPTY_HASH

    def test_empty_file_and_directory_share_digest_but_distinct_content(self) -> None:
        directory = DirectoryContent(hash=_EMPTY_HASH, files=())
        file = FileContent(hash=hash_file(b""))
        absent = AbsentContent()

        assert file.hash == directory.hash == hash_files(directory.file_hashes()) == _EMPTY_HASH
        assert len({file, directory, absent}) == 3

    def test_published_example(self) -> None:
        files = {ProjectPathId("@/guide.md"): _GUIDE_HASH}

        assert hash_files(files) == _DIRECTORY_HASH

    def test_utf8_sorted_nul_delimited_encoding(self) -> None:
        files = {ProjectPathId("@/" + path): _GUIDE_HASH for path in ("é", "Z", "e\u0301")}
        expected_input = (
            b"file\0@/Z\0"
            b"sha256:5ba1677a4833fd02ece676817a073abef834d542a27562e9ad1c903b656d37dd\0"
            b"file\0@/e\xcc\x81\0"
            b"sha256:5ba1677a4833fd02ece676817a073abef834d542a27562e9ad1c903b656d37dd\0"
            b"file\0@/\xc3\xa9\0"
            b"sha256:5ba1677a4833fd02ece676817a073abef834d542a27562e9ad1c903b656d37dd\0"
        )

        for ordering in permutations(files.items()):
            assert hash_files(MappingProxyType(dict(ordering))) == hash_file(expected_input)

    def test_paths_and_child_bytes_affect_identity(self) -> None:
        original = {ProjectPathId("@/guide.md"): _GUIDE_HASH}
        renamed = {ProjectPathId("@/Guide.md"): _GUIDE_HASH}
        edited = {ProjectPathId("@/guide.md"): _EMPTY_HASH}

        assert len({hash_files(files) for files in (original, renamed, edited)}) == 3
        assert hash_files(original) != hash_files(original | renamed)
