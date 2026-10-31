import pydantic
import pytest

from vcsinstall.domain import ContentHash, DependencyName, SourceRevision
from vcsinstall.filesystem import hashes
from vcsinstall.workspace import DependencyLock, Lockfile

_EMPTY_HASH = hashes.hash_file(b"")
_GUIDE_HASH = hashes.hash_file(b"Shared guide.\n")
_DIRECTORY_HASH = ContentHash("sha256:bea5475d87735beab89c88a39fb417625013c60ddf696d3dba4eb11d343e27fe")


class TestDependencyLock:
    @pytest.mark.parametrize("revision", ["1" * 40, "a" * 64])
    @pytest.mark.parametrize("selector", ["branch", "tag", "revision"])
    def test_accepts_complete_git_revisions(self, revision: str, selector: str) -> None:
        value = revision if selector == "revision" else "Release/One"
        lock = DependencyLock.model_validate(
            {
                "vcs": "git",
                "repository": "source",
                "selector": {"kind": selector, "value": value},
                "revision": revision,
            }
        )

        assert lock.revision == revision
        assert lock.selector is not None
        assert lock.selector.value == value
        assert lock.model_dump(mode="json")["selector"] == {"kind": selector, "value": value}

    def test_rejects_invalid_selector_shape(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            DependencyLock.model_validate(
                {
                    "vcs": "git",
                    "repository": "source",
                    "revision": "2" * 40,
                    "selector": {"kind": ["branch", "tag"], "value": "main"},
                }
            )

    def test_live_filesystem_lock_keeps_omitted_selector(self) -> None:
        lock = DependencyLock.model_validate({"vcs": "filesystem", "repository": "source", "revision": _EMPTY_HASH})

        assert lock.selector is None
        assert lock.revision == SourceRevision(_EMPTY_HASH)


class TestLockfile:
    @pytest.mark.parametrize("name,indices", [("ci", (0, 2)), ("formatting", (1,)), ("unused", ()), ("missing", ())])
    def test_entries_for_dependency__selects_in_stored_order(self, name: str, indices: tuple[int, ...]) -> None:
        definition = {"vcs": "noop", "repository": "fixture", "revision": "fixture revision"}
        binding = {"source_path": "@/", "kind": "absent"}
        lockfile = Lockfile.model_validate(
            {
                "version": 1,
                "dependencies": {name: definition for name in ("ci", "formatting", "unused")},
                "entries": [
                    {**binding, "dependency": "ci", "destination": "@/z"},
                    {**binding, "dependency": "formatting", "destination": "@/middle"},
                    {**binding, "dependency": "ci", "destination": "@/a"},
                ],
            }
        )

        assert lockfile.entries_for_dependency(DependencyName(name)) == tuple(
            lockfile.entries[index] for index in indices
        )

    def test_entries_for_dependency__empty_lockfile(self) -> None:
        lockfile = Lockfile.model_validate({"version": 1})

        assert not lockfile.entries_for_dependency(DependencyName("ci"))

    def test_records_all_baseline_kinds_and_roundtrips(self) -> None:
        binding = {"dependency": "ci", "source_path": "@/", "destination": "@/docs"}
        lockfile = Lockfile.model_validate(
            {
                "version": 1,
                "dependencies": {
                    "ci": {
                        "vcs": "git",
                        "repository": "source",
                        "selector": {"kind": "branch", "value": "main"},
                        "revision": "1" * 40,
                    }
                },
                "entries": [
                    {**binding, "destination": "@/license", "kind": "file", "hash": _EMPTY_HASH},
                    {
                        **binding,
                        "kind": "directory",
                        "hash": _DIRECTORY_HASH,
                        "files": [{"path": "@/guide.md", "hash": _GUIDE_HASH}],
                    },
                    {**binding, "destination": "@/absent", "kind": "absent"},
                ],
            }
        )

        assert [entry.kind for entry in lockfile.entries] == ["file", "directory", "absent"]
        assert lockfile.entries_for_dependency(DependencyName("ci")) == lockfile.entries
        assert Lockfile.model_validate_json(lockfile.model_dump_json()) == lockfile

    @pytest.mark.parametrize(
        "content",
        [
            {"kind": "file"},
            {"kind": "file", "hash": _EMPTY_HASH, "files": []},
            {"kind": "absent", "hash": _EMPTY_HASH},
            {"kind": "directory", "hash": _EMPTY_HASH},
            {"kind": "directory", "hash": _DIRECTORY_HASH, "files": []},
            {"kind": "directory", "hash": _EMPTY_HASH, "files": [{"path": "@/x", "hash": _EMPTY_HASH}]},
            {"kind": "directory", "hash": _EMPTY_HASH, "files": [{"path": "@/x", "hash": _EMPTY_HASH}] * 2},
        ],
    )
    def test_rejects_invalid_baselines(self, content: dict[str, object]) -> None:
        with pytest.raises(pydantic.ValidationError):
            Lockfile.model_validate(
                {
                    "version": 1,
                    "dependencies": {
                        "ci": {
                            "vcs": "git",
                            "repository": "source",
                            "selector": {"kind": "branch", "value": "main"},
                            "revision": "1" * 40,
                        }
                    },
                    "entries": [{"dependency": "ci", "source_path": "@/", "destination": "@/docs", **content}],
                }
            )

    @pytest.mark.parametrize(
        "field,value", [("dependency", "missing"), ("source_path", "@/a/../b"), ("destination", "docs")]
    )
    def test_requires_locked_binding_and_canonical_paths(self, field: str, value: str) -> None:
        with pytest.raises(pydantic.ValidationError):
            Lockfile.model_validate(
                {
                    "version": 1,
                    "dependencies": {
                        "ci": {
                            "vcs": "git",
                            "repository": "source",
                            "selector": {"kind": "branch", "value": "main"},
                            "revision": "1" * 40,
                        }
                    },
                    "entries": [
                        {
                            "dependency": "ci",
                            "source_path": "@/",
                            "destination": "@/docs",
                            "kind": "absent",
                            field: value,
                        }
                    ],
                }
            )
