# Dependency locking

## Goal of the document

This document describes how vcsinstall selects and preserves dependency revisions, verifies installed content, and changes installation baselines.

## Scope

This specification covers locking operations and their observable state transitions and consistency guarantees.
The following are out of scope:

- CLI input and output contracts.
- configuration and lockfile serialization schemas.
- repository locator syntax and VCS transport mechanisms.
- cache layout and checkout management.
- module ownership, Python interfaces, and process-coordination mechanisms.

## State and shared revisions

Dependency, entry, and installation-baseline terminology MUST follow `specs/dictionary.md`.
Configuration declarations MUST follow `specs/behavior/config.md`; persisted lock state MUST follow `specs/behavior/lockfile.md`.
Operations MUST apply to the selection established under `specs/behavior/cli.md`.

Each dependency MUST directly bind one backend, normalized repository locator, and requested version selector to its entries.
There MUST NOT be a separate user-managed source or group entity between a dependency and its repository.
Every entry MUST belong to exactly one dependency and obtain repository and version selection from it.
Different dependency names MUST remain independently lockable and updatable, including when they share a repository and selector.
Dependencies without entries MUST remain lockable and independently updatable.

A dependency MUST have at most one shared locked immutable revision.
Every recorded entry baseline MUST describe content at that revision; inconsistent entry baselines MUST fail rather than be accepted as independent entry versions.
Content identities MUST remain specific to entries so local changes can be detected independently.
A successful revision change MUST leave every declared entry of that dependency installed and recorded at the selected shared revision.
An operation selecting a revision to establish or refresh a dependency lock MUST resolve it once per dependency for the invocation, including when the resolved revision is unchanged, and use it for every affected entry.
Unselected dependencies and entries MUST retain their installation state.

Configuration MUST remain the authority for requested dependencies and entry mappings.
A requested immutable revision pin MUST NOT itself establish an installation baseline.
A baseline MUST be established from the selected source revision rather than by accepting arbitrary installed content.
Loading configuration or lock state MUST NOT create declarations or acknowledge local changes as a baseline.
Loading configuration and running `init` MUST NOT create a lockfile or fetch repositories.

## Version selection

Source selectors MUST preserve the selected backend's native semantics under Version resolution and anchoring in `specs/behavior/vcs-interaction.md`.
Branch and tag names MUST remain literal, including case, and MUST select the same named reference rather than a semantic-version range or the newest release.
An immutable revision pin MUST keep selecting that revision even when newer commits exist.
Accepted revision abbreviations MUST resolve unambiguously before the complete immutable identifier is recorded in a declaration.
After selector-kind inference from CLI input, the dependency declaration MUST record the resolved kind and requested name, or the complete immutable identifier for a revision pin.
Later operations MUST use that recorded selector rather than repeat inference.

Establishing a dependency definition without a selector MUST infer the backend's default version under its source contract in `specs/behavior/vcs-interaction.md`.
The inferred Git branch name MUST be recorded as the dependency lock's branch selection.
When `dependency add` establishes or explicitly replaces the Git definition, it MUST also write that branch name into configuration.

For hand-written Git configuration omitting a selector, a matching branch lock MUST supply the effective selector.
Without such a lock, an operation establishing the lock MUST infer and record the default branch.
Operations reusing the lock MUST keep that branch when the remote's default branch changes or the cache is cleared.
Explicit dependency redefinition without a selector MUST perform default-version inference for the new definition.

**Example:** A dependency first locked when the remote's default branch is `main` records that branch.
If the remote's default later becomes `stable`, `dependency update` continues following `main`.

## Matching configuration

A dependency lock MUST match a declaration only when its name, effective repository identity, and effective selector agree.
Repository identity MUST follow the Repository locators and identity contract in `specs/behavior/cli.md`, with configured filesystem paths interpreted under `specs/behavior/config.md`.
A Git declaration omitting a selector MUST match a branch lock for the same dependency name and repository identity, using that recorded branch as its effective selector.
An explicit branch selection MUST match a recorded inferred branch with the same branch name when dependency name and repository identity agree.
A tag or revision lock MUST NOT satisfy an omitted Git selector.
A branch or tag advancing remotely MUST NOT itself make a lock stale relative to configuration.

An entry baseline MUST match a declaration only when its dependency lock matches and its dependency name, source path, and canonical destination agree.
Configuration comments, declaration ordering, and equivalent destination spelling MUST NOT affect matching.
Local content changes MUST NOT affect declaration matching.
A complete configuration match MUST require exactly the declared dependency and entry sets, with a matching record for every declaration.

A missing or mismatching record MUST remain different from malformed lock state.
Prior valid records MUST remain available to protect old installations until replacement succeeds, including after explicit configuration changes.
Using an old binding to verify prior content MUST NOT make it a matching lock for a new definition.
An operation MUST NOT discard or reinterpret a baseline still needed to protect an unselected declared entry; if its selection cannot preserve that protection, it MUST fail before mutation.

## Content identity

Content comparison MUST use the entry's kind and hash, with expected absence compared through kind alone.
Hashes MUST use SHA-256, with their stored representation defined in `specs/behavior/lockfile.md`.
VCS revision identifiers MUST NOT substitute for installed-content hashes.
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

To make content identity interoperable, a directory's SHA-256 input MUST be the concatenation of records for its immediate children, sorted by their literal names encoded as UTF-8 bytes.
Each record MUST concatenate these byte sequences in order:

1. The child's kind, `file` or `directory`, encoded as ASCII, followed by one NUL byte.
2. The child's literal name encoded as UTF-8, followed by one NUL byte.
3. The child's 64-digit lowercase SHA-256 digest encoded as ASCII, without the `sha256:` prefix, followed by one NUL byte.

Child file digests MUST follow the Files contract, and child directory digests MUST follow this same Directories contract recursively.
An empty directory MUST hash an empty byte sequence; its kind MUST distinguish it from an empty file.
Names MUST NOT undergo Unicode normalization, case folding, or separator substitution.

### Expected absence

An absent baseline MUST represent source content recorded as absent by an operation supporting deletions.
It MUST remain distinct from an unexpectedly missing local destination whose baseline is a file or directory.
Deleting a child within a directory entry MUST change the directory's hash while its kind remains `directory`.

**Example:** A project declares `LICENSE` from dependency `ci`, installed at `@/licenses/shared.txt`.

1. The initial baseline records `kind = "file"` and the file's hash.
2. Upstream deletes `LICENSE`; `vcsinstall dependency update ci` removes the unchanged local copy, advances the dependency's revision, and records `kind = "absent"` without a hash.
3. The entry remains declared, and `vcsinstall list` reports it as clean because absence is expected. If upstream later recreates `LICENSE`, a subsequent dependency update restores the file.

Deleting the local copy alone leaves its baseline as a file and produces local status `missing`.
Publishing that deletion leaves the baseline unchanged; update can record absence when the selected source also omits the entry.

### Comparison failures

Content identity MUST NOT depend on:

- timestamps.
- ownership.
- permissions.
- absolute filesystem locations.

Unreadable content, unsupported filesystem objects, and names that cannot be represented by the encoding contract MUST fail rather than be skipped or reported as clean.
Symbolic links MUST NOT be followed in violation of the containment contract in `specs/behavior/paths.md` in `llm-tool-cli`.

## Local state inspection

Inspection MUST compare installed content against matching baselines without fetching sources or accessing the repository cache.
It MUST NOT create or modify project files.
Local status MUST distinguish:

- `clean` — Local content agrees with the matching baseline, including expected absence.
- `modified` — Local content differs from that baseline, including additions, deletions, and file-type changes within a directory entry.
- `missing` — The destination is absent although its matching baseline expects it to exist.
- `unlocked` — No matching record exists, so a trustworthy local-change comparison is unavailable.

A missing lockfile MUST classify declared entries as unlocked without creating one.
An old binding's baseline MUST NOT be presented as the baseline of a new mapping.
Malformed or unreadable state MUST fail rather than produce empty results or invented clean or unlocked states.
Modified, missing, or unlocked status MUST NOT by itself make inspection fail.

## Source integrity and cache independence

When source content is retrieved to reproduce an existing baseline, its kind and hash MUST agree with that baseline before installation.
A mismatch MUST fail rather than rewrite the expected hash, accept the cache's content, or select a newer revision.
Required repository data MUST follow Repository acquisition and refresh in `specs/behavior/vcs-interaction.md`; content inspection MUST follow Source inspection and extraction in that specification, including backend-specific revision-availability limits.
For backends using the repository cache, rebuilding missing cache data MUST follow Repository reuse and revision isolation in `specs/behavior/cache.md`.
Cache reuse and cleanup MUST preserve locked revisions, baselines, and local-content comparisons under `specs/behavior/cache.md`.

## Source freshness

Operations requiring the current target of a default, branch, or tag selector MUST refresh source information before resolving that target.
Current-source observations MUST follow Repository acquisition and refresh in `specs/behavior/vcs-interaction.md`, with selector interpretation following its Version resolution and anchoring contract.
Stale cached references MUST NOT substitute for a failed refresh or selector resolution.
A required refresh or resolution failure MUST cause an operational failure identifying the affected dependency.
Immutable pins MUST NOT require a refresh solely to search for newer commits and MUST NOT become outdated merely because newer commits or release tags exist.

Freshness comparison MUST compare the dependency's locked immutable revision with the current revision resolved under its effective selector, using complete immutable identifiers.
Any differing revision MUST count as outdated, including when source content is identical or the current target is not a descendant of the locked revision.
The comparison MUST NOT advance the lock or select a different revision for an installation using that lock.

## Installation protection

Entry addition and declared installation MUST NOT silently overwrite local modifications or conflicting unmanaged content.
A wholly absent destination MAY be restored from the selected source revision.
Entry redefinition MUST verify existing content against the previous entry baseline before replacing its declaration or lock record.
Success MUST leave affected declarations, lock records, and installed content consistent while preserving unselected project content.

## Updating baselines

Dependency updates and operations adopting their protection MUST compare each selected entry with its prior baseline and the prepared content at the selected source revision under Content identity.
Missing or untrustworthy baselines and source-integrity failures MUST cause operational failure.
For entries with trustworthy baselines, local-content verification MUST follow these rules:

- When local content already matches the selected source content, verification MUST pass even if it differs from the prior baseline. Installed content MUST remain untouched, and success MUST record the selected revision and content identity as the new baseline.
- When local content matches the prior baseline and differs from the selected source content, verification MUST pass, and update MUST replace it with the selected source content.
- When local content differs from both the prior baseline and the selected source content, verification MUST fail without overwriting it or advancing any selected dependency lock.

Directory equality MUST cover the complete tree; matching only some changed files MUST NOT permit accepting the remaining differences.
Only content at the revision selected by the dependency's effective selector MUST be used for this equality check.
The decision MUST depend on content equality rather than publication history, commit ancestry, or evidence that a contribution was accepted.

**Example:** The baseline for an installed guide contains `Original guide.\n`, and its local content is changed to `Clarified guide.\n`.
If the selected upstream revision contains exactly `Clarified guide.\n`, update leaves the local file untouched and records that revision and hash as its baseline, regardless of which machine published the change or how it reached upstream.
If upstream instead contains different content, update fails while the local content still differs from the prior baseline, including when upstream contains the same clarification plus additional edits.

## Operation contracts

### `dependency add`

Declaring a dependency MUST record its definition in configuration, establish its binding, and resolve and lock its selected immutable revision, including when it has no entries.
Resolution MUST use Source freshness for tracking selectors and MAY reuse valid cached data for immutable pins.
Explicit replacement MUST refresh every declared entry at the new shared revision using Updating baselines while preserving dependency membership and entry mappings.
Replacement permission MUST NOT bypass local-content protection or other validation.
An identical replacement requiring no declaration, revision, or content changes MUST succeed as unchanged.

### `add`

Adding an entry MUST install and establish its baseline at an already matching dependency lock without advancing that lock or other entries.
A missing or mismatching dependency lock MUST fail with guidance to run `install` or `dependency update NAME` first.
A source path absent at that locked revision MUST fail, including when an absent baseline could otherwise be represented.

Before installation, Source freshness MUST compare the locked revision with the current target of the effective selector.
A difference MUST fail before project mutation unless the caller explicitly permits use of the outdated locked revision.
That permission MUST NOT bypass a failed freshness check, missing or mismatching lock, unavailable source path, or Installation protection.
Success MUST record the entry mapping in configuration and its content identity at the dependency's shared revision.
An entry replacement requiring no declaration or content changes MUST succeed as unchanged after all checks pass.

### `install`

Declared installation MUST cover every dependency and entry without modifying configuration.
Matching dependency locks MUST be reused even if a source branch or tag has advanced; newly declared entries MUST use those existing revisions.
Each missing or changed dependency binding MUST be resolved, and all its entries MUST use that resulting shared revision without advancing unrelated valid locks.
Dependencies without entries MUST also receive matching locks when needed.
Installation protection and Source integrity and cache independence MUST apply.
When declarations, matching locks, and installed content already agree, installation MUST succeed as unchanged without advancing revisions.

Ordinary installation MUST produce a complete configuration match and remove obsolete records only after successful preparation and installation.
It MUST NOT delete installed content merely because its declaration was manually removed.
Locked installation MUST require an existing complete configuration match before changing installed content and MUST leave lockfile bytes unchanged.
Locked installation MAY fetch missing immutable source content and MUST NOT imply offline execution.

### `dependency update`

Updating MUST include every declared entry of each selected dependency, including locking dependencies with no entries.
The effective selector MUST resolve under Source freshness for each selected dependency.
Updating baselines MUST apply, with shared revisions and entry baselines changed together.
Configuration, repository locations, and requested selectors MUST remain unchanged.
Prior valid bindings MAY verify old installations after explicit configuration changes under Matching configuration.
Source deletions within a mapping MUST be reflected without changing unrelated project content.
Entries whose resolved revision and installed content require no change MUST succeed as unchanged.

### `dependency push`

Publication MUST use the dependency's matching lock and the existing entry baselines without advancing the lock.
Branch, PR, and patch preparation MUST use the locked revision as their base rather than resolve a newer source version for that purpose.
PR target inspection MAY resolve a current review branch for validation without changing the contribution base or installation revision.
Direct delivery MUST prepare against the current destination branch with the recorded baselines under `specs/behavior/hosting/git.md`, without requiring the old locked source content when those baselines are trustworthy.
Filesystem overwrite MAY inspect the live source and resolve its resulting publication revision under `specs/behavior/hosting/filesystem.md`.
Every entry inspected to determine the changed selection MUST have a matching, trustworthy baseline and inspectable local state.
Unavailable or inconsistent baselines and unreadable content MUST fail before source fetching, hosting requests, or delivery effects.
Change detection MUST compare current local content with the installation baseline; a previous push MUST NOT make a locally changed entry appear unchanged.
Unselected entries MUST NOT require local-content verification or contribute changes.
Publication MUST leave configuration, lockfile bytes, and installed content unchanged and MUST NOT maintain persistent publication history.
Patch output MUST remain a separately requested artifact under the CLI contract rather than a change to installation state.

### `remove`

Entry removal MUST discard selected declarations and baselines while preserving dependency declarations and shared locks, including after the last entry is removed.
Missing lock state MUST NOT prevent removal when files are retained, the destination is already absent, or deletion of unverified content is explicitly authorized.
Unselected entries, metadata, and content MUST remain unchanged.
Removed entries MUST stop participating in project operations until declared again.

### `dependency remove`

Dependency removal MUST require an unused dependency and remove its declaration, shared lock, and obsolete entry records without deleting installed content or disturbing unrelated state.
A missing lockfile or dependency lock MUST NOT prevent removal; existing metadata MUST still validate before mutation.
Operations that only remove records MUST NOT create an absent lockfile.

### Read-only operations

`list` and `dependency list` MUST use Local state inspection.
`dependency outdated` MUST require matching selected dependency locks before source fetching, then compare locked and current revisions under Source freshness without requiring installed content or local-change verification.
Its source comparison MAY populate or refresh the repository cache.
These inspection operations MUST NOT change declarations, lock state, or installed content.

## Preparation and consistency

Command validation MUST finish before source fetching as required by `specs/behavior/cli.md`.
Required content checks and source preparation MUST complete for the entire selection before project mutation.
Validation or preparation failure MUST preserve affected declarations, lock records, and installed content.
If any selected entry blocks update preparation, every selected dependency lock and installed entry MUST remain unchanged.
Prior baselines MUST remain in effect until all affected content is successfully installed; partially applied installations MUST NOT be recorded as complete.
Missing lock state MUST follow the applicable operation contract rather than be reconstructed from arbitrary installed content.
If applying changes fails after effects have completed, diagnostics MUST identify the failure and partial effects rather than imply rollback or successful installation.
Locking failure diagnostics MUST identify the affected dependency or entry and the lockfile path as applicable.
Diagnostics MUST distinguish stale declarations from source-integrity failures, retaining loading and filesystem diagnostics under `specs/behavior/lockfile.md`.
Diagnostic rendering and exit statuses MUST follow `specs/behavior/cli.md`; lockfile loading and writing failures MUST follow `specs/behavior/lockfile.md`.
