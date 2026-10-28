# Lockfile

## Goal of the document

This document describes the location, persisted representation, validation, and file I/O contract of vcsinstall's lockfile.

## Scope

This specification covers the lockfile's location, TOML schema, field representations, loading, and writing.
The following are out of scope:

- CLI invocation and output formats.
- project configuration syntax.
- revision selection, content hashing, and locking state transitions.
- VCS fetching and publication mechanisms.
- cache layout and checkout management.
- module ownership and Python interfaces.

## Project state

The lockfile MUST be `vcsinstall.lock` in the selected configuration file's parent directory under `specs/behavior/config.md`.
Selection MUST use that exact sibling path without independent upward discovery or a cache-directory fallback.
The path MUST follow the containment and symbolic-link rules in `specs/behavior/paths.md` in `llm-tool-cli`.
A lockfile symlink or an existing non-regular-file target MUST fail rather than be followed or treated as absent.

The lockfile SHOULD be committed alongside project configuration so another checkout can reproduce the same installation.
The meaning and lifecycle of recorded state MUST follow `specs/behavior/locking.md`.
CLI selection and output MUST follow `specs/behavior/cli.md`.

## TOML structure

The lockfile MUST be UTF-8 TOML under the Configuration loading contract in `specs/behavior/config.md` in `llm-tool-cli`.
The supported top-level fields MUST be:

- `version` — Required integer schema version.
- `dependencies` — Optional table of dependency locks, defaulting empty.
- `entries` — Optional array of installation baselines, defaulting empty.

Tables MUST accept only the fields defined for them in this specification.
The supported lockfile schema version MUST be `1`.
Version validation and diagnostics MUST follow the Version contract in `specs/behavior/config.md` in `llm-tool-cli`; missing versions, including empty documents, MUST fail schema validation.
Lockfile and configuration schema versions MUST be validated independently.
A file containing only `version = 1` MUST be a valid lockfile with no recorded dependencies or entries.

Parsing and schema validation MUST NOT fetch sources, require a VCS executable, access the repository cache, or inspect installed content.
Loading MUST validate recorded structure independently of whether it matches active configuration.
Declaration matching and missing or stale state MUST follow Matching configuration and Operation contracts in `specs/behavior/locking.md`.

## Dependency locks

Each key in `dependencies` MUST be a dependency name under `specs/behavior/config.md`.
Each value MUST be a table with these fields:

- `vcs` — Required nonempty backend name from the recorded declaration.
- `repository` — Required nonempty normalized native repository locator from the recorded declaration.
- `branch` — Optional nonempty source branch name, explicitly selected or inferred.
- `tag` — Optional nonempty requested tag name.
- `rev` — Optional nonempty requested immutable revision pin.
- `revision` — Required nonempty resolved immutable revision identifier.

Repository identity and the mutually exclusive selector fields MUST follow the configuration contract.
Relative filesystem repository locators MUST retain their project-root-relative meaning.
Every Git dependency lock MUST contain exactly one of `branch`, `tag`, and `rev`.
Branch and tag spellings MUST remain literal; they MUST NOT be replaced by resolved commits in their selector fields.

`revision` MUST use the backend's complete immutable representation under the source contracts identified in `specs/behavior/vcs-interaction.md`.
For an immutable `rev` selector, `revision` MUST identify that same pinned revision.
Inference and shared-revision semantics MUST follow Version selection and State and shared revisions in `specs/behavior/locking.md`.

## Installation baselines

Each item in `entries` MUST be a table with these fields:

- `dependency` — Required name of a dependency recorded in this lockfile.
- `source_path` — Required canonical `@/` source identifier under the configuration's Source paths contract.
- `destination` — Required canonical `@/` identifier under the configuration's destination contract.
- `kind` — Required content kind: `file`, `directory`, or `absent`.
- `hash` — Required content hash for `file` and `directory`; omitted for `absent`.
- `files` — Required array of file-manifest records for `directory`, including an empty array for an empty selection; omitted for `file` and `absent`.

Recorded destinations MUST satisfy the uniqueness, overlap, and project-file conflict constraints in Destinations and entry identity in `specs/behavior/config.md`, independently of whether the records match current declarations.
Record fields MUST retain the binding and mapping used to establish each baseline under State and shared revisions and Matching configuration in `specs/behavior/locking.md`.

### Directory file manifests

Each item in an entry's `files` MUST be a table containing only:

- `path` — Required canonical `@/` identifier scoped to the entry root under File manifests in `specs/behavior/files-and-directories.md`.
- `hash` — Required file content hash under Hash representation below.

Manifest path uniqueness and file-ancestor constraints MUST follow that File manifests contract.
Directory records MUST NOT appear among these child records.
Parsing MUST reject duplicate or conflicting child paths and an aggregate entry hash inconsistent with the recorded file manifest under Directory hashes in `specs/behavior/files-and-directories.md`.
This validation MUST use recorded data alone, without inspecting installed files, sources, or cache data.
Writers SHOULD sort child records by their literal UTF-8 path bytes.

## Hash representation

A hash MUST be `sha256:` followed by exactly 64 lowercase hexadecimal digits.
Digest computation and file-set equality MUST follow Content identity and Local comparison in `specs/behavior/files-and-directories.md`.
Expected-absence baseline transitions MUST follow Expected absence in `specs/behavior/locking.md`.

## Writes and failures

Writers SHOULD use deterministic field and record ordering so identical logical changes produce stable diffs.
State that requires no logical change MUST leave the lockfile's bytes unchanged.
Generated state MUST NOT include cache paths, timestamps, or machine-specific checkout locations.
Repository locators MUST retain their declared meaning, including filesystem sources, under the configuration contract.

Lockfile replacement MUST be atomically visible; failed serialization or replacement MUST preserve the previous file, or leave an initially absent file absent.
A concurrent lockfile change MUST NOT be silently overwritten with state prepared from an older version.
State consistency before and after writes MUST follow Preparation and consistency in `specs/behavior/locking.md`.

Malformed or unreadable lockfiles MUST fail rather than be replaced with empty state or reported as unlocked.
Diagnostics MUST identify the lockfile path and distinguish absence, invalid content, and filesystem failures as applicable.
Schema diagnostics MUST identify the affected field or record, and adopted shared loading and containment diagnostics MUST retain their native information.

## Example

This example records two entries sharing one Git revision.
The file contains the bytes `Shared license.\n`; the directory contains only `guide.md` with the bytes `Shared guide.\n`.
The Git revision identifiers are illustrative.

```toml
version = 1

[dependencies.ci]
vcs = "git"
repository = "https://example.com/team/shared.git"
branch = "main"
revision = "1111111111111111111111111111111111111111"

[[entries]]
dependency = "ci"
source_path = "@/LICENSE"
destination = "@/licenses/shared.txt"
kind = "file"
hash = "sha256:4745da00e984a8aab4faeb69b987318e1aabdd8fad0df4a22f2a89a865299cc1"

[[entries]]
dependency = "ci"
source_path = "@/specs"
destination = "@/specs/shared"
kind = "directory"
hash = "sha256:bea5475d87735beab89c88a39fb417625013c60ddf696d3dba4eb11d343e27fe"

[[entries.files]]
path = "@/guide.md"
hash = "sha256:5ba1677a4833fd02ece676817a073abef834d542a27562e9ad1c903b656d37dd"
```
