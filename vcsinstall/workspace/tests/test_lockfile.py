import pydantic
import pytest
from llm_tool_cli.paths import ProjectPathId

from vcsinstall.domain import ContentHash, DependencyName, SourceRevision
from vcsinstall.domain.tests import make
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
    @pytest.fixture
    def lockfile(self) -> Lockfile:
        definition = {"vcs": "noop", "repository": "fixture", "revision": "recorded revision"}
        return Lockfile.model_validate(
            {
                "version": 1,
                "dependencies": {name: definition for name in ("ci", "formatting")},
                "entries": [
                    {"dependency": "ci", "source_path": "@/guide", "destination": "@/docs/guide", "kind": "absent"},
                    {
                        "dependency": "formatting",
                        "source_path": "@/LICENSE",
                        "destination": "@/licenses/shared",
                        "kind": "absent",
                    },
                ],
            }
        )

    @pytest.mark.parametrize("name", ["ci", "new"])
    def test_with_dependency__adds_or_replaces_a_lock_without_changing_baselines(
        self, lockfile: Lockfile, name: str
    ) -> None:
        before = lockfile.model_dump()
        dependency = DependencyLock(
            **make.dependency(vcs="noop", repository="replacement").model_dump(),
            revision=SourceRevision("new revision"),
        )

        updated = lockfile.with_dependency(DependencyName(name), dependency)

        assert updated.dependencies[DependencyName(name)] == dependency
        assert (
            updated.dependencies[DependencyName("formatting")] == lockfile.dependencies[DependencyName("formatting")]
        )
        assert tuple(updated.dependencies) == (("ci", "formatting") if name == "ci" else ("ci", "formatting", "new"))
        assert updated.entries == lockfile.entries
        assert lockfile.model_dump() == before

    def test_with_baselines__replaces_in_place_and_appends_in_input_order(self, lockfile: Lockfile) -> None:
        before = lockfile.model_dump()
        changed = lockfile.entries[0].replace(
            dependency=DependencyName("formatting"), source_path=ProjectPathId("@/changed")
        )
        first_new = lockfile.entries[0].replace(destination=ProjectPathId("@/z"))
        second_new = lockfile.entries[0].replace(destination=ProjectPathId("@/a"))

        updated = lockfile.with_baselines(baseline for baseline in (first_new, changed, second_new))

        assert updated.entries == (changed, lockfile.entries[1], first_new, second_new)
        assert updated.dependencies == lockfile.dependencies
        assert lockfile.model_dump() == before

    @pytest.mark.parametrize("destination", ["@/docs/guide", "@/new"])
    def test_with_baselines__repeated_destination_uses_the_last_baseline(
        self, lockfile: Lockfile, destination: str
    ) -> None:
        before = lockfile.model_dump()
        first = lockfile.entries[0].replace(destination=ProjectPathId(destination))
        last = first.replace(source_path=ProjectPathId("@/last"))

        updated = lockfile.with_baselines((first, last))

        expected = (last, lockfile.entries[1]) if destination == "@/docs/guide" else (*lockfile.entries, last)
        assert updated.entries == expected
        assert updated.dependencies == lockfile.dependencies
        assert lockfile.model_dump() == before

    def test_with_baselines__empty_updates_preserve_records(self, lockfile: Lockfile) -> None:
        updated = lockfile.with_baselines(())

        assert updated == lockfile
        assert updated is not lockfile

    @pytest.mark.parametrize(
        "destinations,retained",
        [
            ([], (0, 1)),
            (["@/docs/guide"], (1,)),
            (["@/docs"], (0, 1)),
            (["@/missing"], (0, 1)),
            (["@/docs/guide", "@/docs/guide"], (1,)),
            (["@/licenses/shared", "@/docs/guide"], ()),
        ],
    )
    def test_without_entries__removes_only_exact_destinations_and_keeps_shared_locks(
        self, lockfile: Lockfile, destinations: list[str], retained: tuple[int, ...]
    ) -> None:
        before = lockfile.model_dump()

        updated = lockfile.without_entries(tuple(ProjectPathId(path) for path in destinations))

        assert updated.entries == tuple(lockfile.entries[index] for index in retained)
        assert updated.dependencies == lockfile.dependencies
        assert updated is not lockfile
        assert lockfile.model_dump() == before

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
        assert lockfile.baseline_for_destination(ProjectPathId("@/missing")) is None

    @pytest.mark.parametrize(
        "destination,index",
        [
            ("@/first", 0),
            ("@/second", 1),
            ("@/first/child", None),
            ("@/missing", None),
        ],
    )
    def test_baseline_for_destination__selects_only_an_exact_destination(
        self, destination: str, index: int | None
    ) -> None:
        lockfile = Lockfile.model_validate(
            {
                "version": 1,
                "dependencies": {"ci": {"vcs": "noop", "repository": "fixture", "revision": "recorded revision"}},
                "entries": [
                    {"dependency": "ci", "source_path": "@/", "destination": "@/first", "kind": "absent"},
                    {"dependency": "ci", "source_path": "@/", "destination": "@/second", "kind": "absent"},
                ],
            }
        )

        result = lockfile.baseline_for_destination(ProjectPathId(destination))

        assert result is (lockfile.entries[index] if index is not None else None)

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
