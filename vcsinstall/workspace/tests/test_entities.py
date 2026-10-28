import pydantic
import pytest
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash
from vcsinstall.filesystem import hashes
from vcsinstall.workspace import (
    AbsentContent,
    Content,
    ContentSchema,
    DirectoryContent,
    FileContent,
    ManifestFile,
)

_EMPTY_HASH = hashes.hash_file(b"")
_GUIDE_HASH = hashes.hash_file(b"Shared guide.\n")
_DIRECTORY_HASH = ContentHash("sha256:bea5475d87735beab89c88a39fb417625013c60ddf696d3dba4eb11d343e27fe")


class TestContentSchema:
    @pytest.mark.parametrize(
        "data,expected",
        [
            ({"kind": "file", "hash": _EMPTY_HASH}, FileContent(hash=_EMPTY_HASH)),
            ({"kind": "directory", "hash": _EMPTY_HASH, "files": []}, DirectoryContent(hash=_EMPTY_HASH, files=())),
            ({"kind": "absent"}, AbsentContent()),
            (
                {"kind": "directory", "hash": _DIRECTORY_HASH, "files": [{"path": "@/guide.md", "hash": _GUIDE_HASH}]},
                DirectoryContent(
                    hash=_DIRECTORY_HASH, files=(ManifestFile(path=ProjectPathId("@/guide.md"), hash=_GUIDE_HASH),)
                ),
            ),
        ],
    )
    def test_validate__returns_content_values(self, data: dict[str, object], expected: Content) -> None:
        adapter = pydantic.TypeAdapter(ContentSchema)

        content = adapter.validate_python(data)

        assert content == expected
        assert content.model_dump(mode="json") == data
        assert adapter.validate_json(content.model_dump_json()) == content

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"hash": _EMPTY_HASH},
            {"hash": _EMPTY_HASH, "files": []},
            {"kind": "file"},
            {"kind": "file", "hash": _EMPTY_HASH, "files": []},
            {"kind": "directory", "hash": _EMPTY_HASH},
            {"kind": "directory", "files": []},
            {"kind": "absent", "hash": _EMPTY_HASH},
            {"kind": "absent", "files": []},
            {"kind": "symlink", "hash": _EMPTY_HASH},
        ],
    )
    def test_validate__rejects_inconsistent_kind_fields(self, data: dict[str, object]) -> None:
        with pytest.raises(pydantic.ValidationError):
            pydantic.TypeAdapter(ContentSchema).validate_python(data)

    @pytest.mark.parametrize("kind", ["file", "directory"])
    @pytest.mark.parametrize("value", [" " + _EMPTY_HASH, _EMPTY_HASH.upper(), "sha256:" + "0" * 63, b"hash"])
    def test_validate__rejects_invalid_stored_hash(self, kind: str, value: object) -> None:
        data: dict[str, object] = {"kind": kind, "hash": value}
        if kind == "directory":
            data["files"] = []

        with pytest.raises(pydantic.ValidationError) as caught:
            pydantic.TypeAdapter(ContentSchema).validate_python(data)

        assert caught.value.errors()[0]["loc"] == (kind, "hash")

    @pytest.mark.parametrize(
        "files",
        [(), (ManifestFile(path=ProjectPathId("@/guide.md"), hash=_GUIDE_HASH),)],
    )
    def test_validate__rejects_inconsistent_stored_hash(self, files: tuple[ManifestFile, ...]) -> None:
        with pytest.raises(pydantic.ValidationError):
            pydantic.TypeAdapter(ContentSchema).validate_python(
                {"kind": "directory", "hash": "sha256:" + "0" * 64, "files": files}
            )

    @pytest.mark.parametrize("paths", [("a", "a"), ("a", "a/b"), ("a/b/c", "a"), ("a", "a-", "a/b")])
    def test_validate__rejects_conflicting_recorded_files(self, paths: tuple[str, ...]) -> None:
        files = [{"path": "@/" + path, "hash": _EMPTY_HASH} for path in paths]

        with pytest.raises(pydantic.ValidationError) as caught:
            pydantic.TypeAdapter(ContentSchema).validate_python(
                {"kind": "directory", "hash": _EMPTY_HASH, "files": files}
            )

        assert caught.value.errors()[0]["loc"] == ("directory", "files")

    @pytest.mark.parametrize("path", ["guide.md", ".", "../file", "/absolute", "@/", "@/a//b", "@/a/../b"])
    def test_validate__retains_nested_manifest_error_locations(self, path: str) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            pydantic.TypeAdapter(ContentSchema).validate_python(
                {"kind": "directory", "hash": _EMPTY_HASH, "files": [{"path": path, "hash": _EMPTY_HASH}]}
            )

        assert caught.value.errors()[0]["loc"] == ("directory", "files", 0, "path")

    @pytest.mark.parametrize("extra", [{"kind": "directory"}, {"revision": "some-revision"}, {"files": []}])
    def test_validate__rejects_non_file_child_records(self, extra: dict[str, object]) -> None:
        with pytest.raises(pydantic.ValidationError):
            pydantic.TypeAdapter(ContentSchema).validate_python(
                {
                    "kind": "directory",
                    "hash": _DIRECTORY_HASH,
                    "files": [{"path": "@/guide.md", "hash": _GUIDE_HASH, **extra}],
                }
            )

    def test_dump__retains_directory_hash_and_sorted_paths(self) -> None:
        files = (
            ManifestFile(path=ProjectPathId("@/é"), hash=_GUIDE_HASH),
            ManifestFile(path=ProjectPathId("@/e\u0301"), hash=_EMPTY_HASH),
        )
        content = DirectoryContent(hash=hashes.hash_files({file.path: file.hash for file in files}), files=files)

        data = content.model_dump(mode="json")

        assert data["hash"] == content.hash
        assert [file["path"] for file in data["files"]] == ["@/e\u0301", "@/é"]
        assert pydantic.TypeAdapter(ContentSchema).validate_python(data) == content


class TestManifestFile:
    @pytest.mark.parametrize(
        "path",
        [
            "guide.md",
            "examples/basic.py",
            ".hidden",
            "~/literal/*.md",
            "@/literal",
            " notes/with spaces.md ",
            "a+b%20c",
            "Заметки/é.md",
            "notes/e\u0301.md",
            "notes/line\nbreak.txt",
            "notes/back\\slash.txt",
            "a/.../b",
        ],
    )
    def test_preserves_literal_paths_through_serialization(self, path: str) -> None:
        file = ManifestFile(path=ProjectPathId("@/" + path), hash=_EMPTY_HASH)

        restored = ManifestFile.model_validate_json(file.model_dump_json())

        assert restored.path == "@/" + path
        assert file.model_dump(mode="json") == {"path": "@/" + path, "hash": _EMPTY_HASH}
        assert restored == file

    @pytest.mark.parametrize(
        "path",
        [
            "",
            ".",
            "..",
            "guide.md",
            "@/",
            "/guide.md",
            "@/guide.md/",
            "@/./guide.md",
            "@/../guide.md",
            "@/a//b",
            "@/a/./b",
            "@/a/../b",
            "@/a/.",
            "@/a/..",
            b"@/guide.md",
            None,
            1,
        ],
    )
    def test_rejects_invalid_paths(self, path: object) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            ManifestFile.model_validate({"path": path, "hash": _EMPTY_HASH})

        assert caught.value.errors()[0]["loc"] == ("path",)


class TestDirectoryContent:
    @pytest.mark.parametrize("paths", [("a", "a"), ("a", "a/b"), ("a/b/c", "a"), ("a", "a-", "a/b")])
    def test_rejects_duplicate_and_ancestor_paths(self, paths: tuple[str, ...]) -> None:
        files = tuple(ManifestFile(path=ProjectPathId("@/" + path), hash=_EMPTY_HASH) for path in paths)

        with pytest.raises(pydantic.ValidationError) as caught:
            DirectoryContent(hash=hashes.hash_files({file.path: file.hash for file in files}), files=files)

        assert caught.value.errors()[0]["loc"] == ("files",)

    def test_canonicalizes_order_without_normalizing_names(self) -> None:
        paths = ("é", "e\u0301", "a/b", "A", "a-", "a/bb", "a/bc/cousin.txt")
        files = tuple(ManifestFile(path=ProjectPathId("@/" + path), hash=_EMPTY_HASH) for path in paths)
        directory = DirectoryContent(hash=hashes.hash_files({file.path: file.hash for file in files}), files=files)
        reversed_directory = DirectoryContent(
            hash=hashes.hash_files({file.path: file.hash for file in files}), files=tuple(reversed(files))
        )

        assert directory == reversed_directory
        assert [file.path for file in directory.files] == [
            "@/A",
            "@/a-",
            "@/a/b",
            "@/a/bb",
            "@/a/bc/cousin.txt",
            "@/e\u0301",
            "@/é",
        ]
        assert DirectoryContent.model_validate_json(directory.model_dump_json()) == directory

    def test_preserves_nested_error_locations(self) -> None:
        with pytest.raises(pydantic.ValidationError) as caught:
            DirectoryContent.model_validate({"hash": _EMPTY_HASH, "files": [{"path": ".", "hash": _EMPTY_HASH}]})

        assert caught.value.errors()[0]["loc"] == ("files", 0, "path")

    def test_file_hashes__retains_child_positions(self) -> None:
        files = (
            ManifestFile(path=ProjectPathId("@/" + "nested/guide.md"), hash=_GUIDE_HASH),
            ManifestFile(path=ProjectPathId("@/" + "guide.md"), hash=_EMPTY_HASH),
        )
        content = DirectoryContent(hash=hashes.hash_files({file.path: file.hash for file in files}), files=files)

        assert content.file_hashes() == {"@/guide.md": _EMPTY_HASH, "@/nested/guide.md": _GUIDE_HASH}

    def test_file_hashes__empty_manifest(self) -> None:
        assert not DirectoryContent(hash=_EMPTY_HASH, files=()).file_hashes()


class TestFileContent:
    def test_file_hashes__uses_entry_root(self) -> None:
        assert FileContent(hash=_GUIDE_HASH).file_hashes() == {"@/": _GUIDE_HASH}


class TestAbsentContent:
    def test_file_hashes__no_files(self) -> None:
        assert not AbsentContent().file_hashes()
