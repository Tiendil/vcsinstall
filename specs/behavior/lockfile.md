# Lockfile

## Goal of the document

This document describes vcsinstall's durable installation state and its use for reproducible installation, local-change detection, and baseline updates.

## Scope

This specification covers the lockfile's location, schema, content identity, declaration matching, and lifecycle.
The following are out of scope:

- CLI invocation and output formats.
- project configuration syntax.
- VCS fetching and publication mechanisms.
- cache layout and checkout management.
- module ownership and Python interfaces.

## Project state

The lockfile MUST be `vcsinstall.lock` in the selected configuration file's parent directory under `specs/behavior/cli.md`.
Selection MUST use that exact sibling path without independent upward discovery or a cache-directory fallback.
The path MUST follow the containment and symbolic-link rules in `specs/behavior/paths.md` in `llm-tool-cli`.
A lockfile symlink or an existing non-regular-file target MUST fail rather than be followed or treated as absent.

The lockfile MUST contain generated project state independently of disposable repository caches.
It MUST retain the information needed for offline local-change detection after `cache clean`.
It SHOULD be committed alongside the project configuration so another checkout can reproduce the same installation.
Merely loading configuration or running `init` MUST NOT create a lockfile.

Configuration MUST remain the authority for requested declarations under `specs/behavior/config.md`.
Command selection, mutation permissions, and local-content protection MUST follow `specs/behavior/cli.md`.
Loading a lockfile MUST NOT create declarations or acknowledge current local content as a new baseline.

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
A structurally valid lockfile MAY be incomplete or stale relative to the current configuration.
Such differences MUST follow the matching and command-specific rules below rather than be treated as syntax errors.

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
For Git, an omitted selector MUST resolve to the remote's default branch name when a dependency is first locked, and the lock MUST record that name as `branch`.
Every Git dependency lock MUST contain exactly one of `branch`, `tag`, and `rev`.
Operations reusing a matching dependency lock MUST retain its inferred branch while configuration omits a selector.
Explicit dependency redefinition MUST follow the CLI source-selection contract.
Branch and tag spellings MUST remain literal; they MUST NOT be replaced by resolved commits in their selector fields.

**Example:** A dependency first locked when the remote's default branch is `main` records `branch = "main"`.
If the remote's default later becomes `stable`, `dependency update` continues following `main`.

`revision` MUST use the backend's complete immutable representation; Git MUST use a full commit hash.
For an immutable `rev` selector, `revision` MUST identify that same pinned revision.
Backend revision identifiers MUST NOT be substituted for the installed-content hashes defined below.

Each dependency MUST have at most one shared locked revision, including dependencies with no entries.
Every installation baseline referring to it MUST describe content at that revision; entry records MUST NOT select independent revisions.
Dependencies sharing a repository or selector MUST retain separate locks under their declared names.

## Installation baselines

Each item in `entries` MUST be a table with these fields:

- `dependency` — Required name of a dependency recorded in this lockfile.
- `source_path` — Required source path under the configuration's Source paths contract.
- `destination` — Required canonical `@/` identifier under the configuration's destination contract.
- `kind` — Required content kind: `file`, `directory`, or `absent`.
- `hash` — Required content hash for `file` and `directory`; omitted for `absent`.

Canonical destinations MUST be unique, and recorded destinations MUST NOT overlap or conflict with the selected configuration or lockfile.
Dependency names, source paths, and destinations MUST describe the mapping used to establish the baseline, even when the current configuration has since changed.
A baseline MUST be established from the selected source revision, not by hashing arbitrary existing destination content and accepting it as installed.

`absent` MUST represent source content recorded as absent by an operation supporting deletions.
It MUST remain distinct from an unexpectedly missing local destination whose baseline is a file or directory.
It MUST NOT make an absent source path valid for `add` under that command's source-availability rules.

**Example:** A project declares `LICENSE` from dependency `ci`, installed at `@/licenses/shared.txt`.

1. The initial baseline records `kind = "file"` and the file's hash.
2. Upstream deletes `LICENSE`; `vcsinstall dependency update ci` removes the unchanged local copy, advances the dependency's revision, and records `kind = "absent"` without a hash.
3. The entry remains declared, and `vcsinstall list` reports it as clean because absence is expected. If upstream later recreates `LICENSE`, a subsequent dependency update restores the file.

Deleting the local copy alone leaves its installation baseline as `file` and produces local status `missing`.
Publishing that deletion leaves the installation baseline unchanged; update can record `absent` when the selected source also omits the entry.
Deleting a child within a directory entry changes the directory's hash while its kind remains `directory`.

## Content identity

Content comparison MUST use both `kind` and `hash`, with absence compared through `kind` alone.
A hash MUST be `sha256:` followed by exactly 64 lowercase hexadecimal digits.
The same content MUST produce the same identity independently of the VCS backend, cache location, and destination name.

### Files

A file hash MUST be SHA-256 of its exact bytes.
Hashing MUST NOT normalize line endings, decode text, or apply VCS filters.

### Directories

A directory hash MUST cover its complete installed tree, including:

- file contents.
- literal relative names.
- file and directory kinds.
- empty directories.
- hidden and locally added content.

Ignore patterns MUST NOT exclude installed content from comparison.
Directory enumeration order MUST NOT affect the hash.

To make the persisted hash interoperable, a directory's SHA-256 input MUST be the concatenation of records for its immediate children, sorted by their literal names encoded as UTF-8 bytes.
Each record MUST concatenate these byte sequences in order:

1. The child's kind, `file` or `directory`, encoded as ASCII, followed by one NUL byte.
2. The child's literal name encoded as UTF-8, followed by one NUL byte.
3. The child's 64-digit lowercase SHA-256 digest encoded as ASCII, without the `sha256:` prefix, followed by one NUL byte.

Child file digests MUST follow the Files contract, and child directory digests MUST follow this same Directories contract recursively.
An empty directory MUST hash an empty byte sequence; its `kind` MUST distinguish it from an empty file.
Names MUST NOT undergo Unicode normalization, case folding, or separator substitution.

### Comparison and integrity failures

Content identity MUST NOT depend on timestamps, ownership, permissions, or absolute filesystem locations.
Unreadable content, unsupported filesystem objects, and names that cannot be represented by the encoding contract MUST fail rather than be skipped or reported as clean.
Symbolic links MUST NOT be followed in violation of the shared containment contract.

When source content is retrieved to reproduce an existing baseline, its kind and hash MUST agree with that baseline before installation.
A mismatch MUST fail rather than rewrite the expected hash, accept the cache's content, or select a newer revision.
Absent cache data MAY be fetched again, but inability to retrieve required source content MUST remain an operational failure.
Offline local-change detection MUST compare installed content directly against recorded baselines without retrieving source content.

## Matching configuration

A dependency lock MUST match a declaration only when its name, effective repository identity, and effective selector agree.
For a Git declaration omitting a selector, a branch lock with the same name and repository identity MUST supply that effective selector and match the declaration.
An explicitly selected branch MUST match a recorded inferred branch with the same name.
A tag or revision lock MUST NOT satisfy an omitted Git selector.
A branch or tag advancing remotely MUST NOT itself make a lock stale relative to configuration; freshness MUST be checked only where required by a command.

An entry baseline MUST match a declaration only when its dependency lock matches and its dependency name, source path, and canonical destination agree.
Configuration comments, declaration ordering, and equivalent destination spelling MUST NOT affect matching.
An entry's local modifications MUST NOT change whether its declarations match the lock.
The lockfile MUST match the complete configuration only when the declared dependency and entry sets match exactly and every declaration has a matching record.

A missing or mismatching record MUST remain different from a malformed record.
Listing commands MUST report unmatched declared entries as `unlocked` under the CLI contract while retaining matching records for other entries.
They MUST NOT present an old binding's baseline as the baseline of a new mapping.

Prior valid records MUST remain available to protect the old installation until its replacement succeeds.
Their use to verify old content MUST NOT make them matching locks for changed declarations.
An operation MUST NOT discard or reinterpret a prior baseline still needed to protect an unselected declared entry; if its permitted selection cannot preserve that protection, it MUST fail before mutation.

## Updating baselines

Dependency update and commands adopting its local-content protection MUST use the Content identity contract to compare each selected entry with its prior baseline and the prepared content at the selected source revision.
Missing or untrustworthy baselines and source-integrity failures MUST retain their CLI failure behavior.
For entries with trustworthy baselines, local-content verification MUST follow these rules:

- When local content already matches the selected source content, verification MUST pass even if it differs from the prior baseline. Installed content MUST remain untouched, and successful update MUST record the selected revision and content identity as the new baseline.
- When local content matches the prior baseline and differs from the selected source content, verification MUST pass, and update MUST replace it with the selected source content.
- When local content differs from both the prior baseline and the selected source content, verification MUST fail without overwriting it or advancing any selected dependency lock.

Equality MUST include the entry's kind and hash, with absence compared through kind alone.
For a directory entry, equality MUST cover the complete tree; matching only some changed files MUST NOT permit accepting the remaining differences.
The decision MUST depend on content equality rather than publication history, commit ancestry, or evidence that a contribution was accepted.
Baseline changes MUST occur only after preparation and content verification succeed for the command's entire selection under the CLI shared-lock guarantees.

**Example:** The baseline for an installed guide contains `Original guide.\n`, and its local content is changed to `Clarified guide.\n`.
If the selected upstream revision contains exactly `Clarified guide.\n`, update leaves the local file untouched and records that revision and hash as its baseline, regardless of which machine published the change or how it reached upstream.
If upstream instead contains different content, update fails while the local content still differs from the prior baseline, including when upstream contains the same clarification plus additional edits.

## Lockfile lifecycle

Commands MUST mutate only the state authorized by their CLI contracts.
In particular:

- `dependency add` MUST record the new binding and shared revision, including when there are no entries; replacement MUST refresh its entries together.
- `add` MUST record its entry baseline at an already matching dependency lock without advancing that lock.
- Ordinary `install` MUST produce a lockfile matching the complete configuration, reusing matching dependency revisions and resolving missing or changed bindings as specified by the CLI.
- `install --locked` MUST require an existing complete matching lockfile and MUST leave its bytes unchanged.
- `dependency update` MUST replace selected dependency revisions and their entry baselines together while preserving unselected state.
- `dependency push` MUST leave the lockfile unchanged.
- `remove` MUST remove selected entry baselines while retaining dependency locks.
- `dependency remove` MUST remove the unused dependency lock and its obsolete entry records without disturbing other dependencies or deleting installed content.
- `list`, `dependency list`, and `dependency outdated` MUST NOT create or modify the lockfile.
- `cache clean` MUST preserve the lockfile.

Ordinary install MUST remove obsolete records only after successful preparation and installation; it MUST NOT delete installed content merely because its declaration was manually removed.
Operations that only remove records MUST NOT create an absent lockfile.
A missing lockfile MUST follow each command's recovery or refusal contract rather than be silently reconstructed from current installed content.

## Writes and failures

Writers SHOULD use deterministic field and record ordering so identical logical changes produce stable diffs.
State that requires no logical change MUST leave the lockfile's bytes unchanged.
Generated state MUST NOT include cache paths, timestamps, or machine-specific checkout locations.
Repository locators MUST retain their declared meaning, including filesystem sources, under the configuration contract.

Lockfile replacement MUST be atomically visible; failed serialization or replacement MUST preserve the previous file, or leave an initially absent file absent.
A concurrent lockfile change MUST NOT be silently overwritten with state prepared from an older version.
Required validation, content checks, and source preparation MUST complete for the command's entire selection before project mutation.
Prior baselines MUST remain in effect until all affected content has been successfully installed; a partially applied installation MUST NOT be recorded as complete.
If applying project changes fails, the command MUST report the failure and any partial effects rather than imply rollback or successful installation.

Malformed or unreadable lockfiles MUST fail rather than be replaced with empty state or reported as unlocked.
Diagnostics MUST identify the lockfile path and distinguish absence, invalid content, stale declarations, integrity failures, and filesystem failures as applicable.
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
source_path = "LICENSE"
destination = "@/licenses/shared.txt"
kind = "file"
hash = "sha256:4745da00e984a8aab4faeb69b987318e1aabdd8fad0df4a22f2a89a865299cc1"

[[entries]]
dependency = "ci"
source_path = "specs"
destination = "@/specs/shared"
kind = "directory"
hash = "sha256:0882e94f4f02b979246240aeb542967a48efb157f10bb67860415d2f907ee2e8"
```
