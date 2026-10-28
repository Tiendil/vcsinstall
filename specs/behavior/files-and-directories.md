# Files and directories

## Goal of the document

This document describes which files vcsinstall manages and how directory mappings are selected, compared, and changed while preserving unmanaged files.

## Scope

This specification covers file selection, manifest meaning, directory creation and removal, content identity, and preservation guarantees at local filesystem and source-content boundaries.
The following are out of scope:

- configuration, lockfile, and CLI syntax.
- dependency revision selection and installation-baseline transitions.
- repository acquisition, cache organization, and delivery modes.
- module ownership and filesystem transaction mechanisms.

## Shared contracts

Terminology MUST follow `specs/dictionary.md`.
Source paths and backend content roots MUST follow Source paths in `specs/behavior/vcs-interaction.md`.
Local access MUST follow the containment and symbolic-link contracts in `specs/behavior/paths.md` in `llm-tool-cli`.
Baseline establishment and operation-specific verification MUST follow `specs/behavior/locking.md`.
Persisted manifests and hash representations MUST follow `specs/behavior/lockfile.md`.

## Configuration and lockfile roles

The selected project configuration file, `vcsinstall.toml` by default, declares the requested installation under Dependencies and Entries in `specs/behavior/config.md`.
Its sibling `vcsinstall.lock` records the source state used to establish installation baselines under Dependency locks and Installation baselines in `specs/behavior/lockfile.md`.
The following summarizes their responsibilities; field definitions and validation remain owned by those specifications.

| Information | Project configuration | Lockfile |
| --- | --- | --- |
| Dependency source | Declares the backend and repository. | Retains the source binding used for the recorded baselines. |
| Version | Declares a requested selector, which may be omitted for default selection. | Records the effective selector and one resolved immutable `revision` shared by the dependency's entries. |
| Entry mapping | Declares one entry for an installed directory root, or one entry for a standalone file. A directory's child files have no separate configuration entries. | Retains one baseline per declared entry; a directory's child file records are nested in that baseline's `files` manifest. |
| File content | Declares the file mapping without a content hash. | Records `kind = "file"` and the root file's `hash`. |
| Directory content | Declares one directory-root mapping; its child file set is discovered from the selected source state. | Records `kind = "directory"`, the aggregate `hash`, and the `files` manifest containing canonical file paths scoped to the entry root and individual hashes. |
| Expected source absence | Retains the entry declaration. | Records `kind = "absent"` without a hash or file manifest. |

The lockfile's directory manifests identify the recorded managed file set for installation and removal; configuration does not expand a directory mapping into declarations for every child.
Publication of a directory selects its complete current local file tree under Contributions, including files not yet present in the recorded manifest.
An empty directory baseline has an empty `files` array, and descendant directories have no independent manifest records.
Actual installed bytes remain at the configured destinations and are compared with these recorded baselines.
Unmanaged local files and local modifications are not added to either project file merely because they exist on disk.

Configuration remains the authority for requested mappings, while previous lock records protect the prior installation until a successful operation replaces its baseline under State and shared revisions and Matching configuration in `specs/behavior/locking.md`.
An explicit revision pin in configuration does not itself establish a baseline.
Loading either file does not acknowledge local changes or rebuild a missing manifest from the destination.

**Example:** Configuration maps source directory `@/guides` to `@/docs/shared` as one entry.
The lockfile records revision A and a directory manifest containing `@/guide.md` and `@/examples/basic.py` with their hashes.
A generated local cache file remains outside that manifest.
If an update selects a revision containing another source file, the successful update records the new manifest while preserving the same configured directory mapping.

## Source file selection

Initial installation and revision changes MUST select files from the requested immutable source state under the selected backend's content contract.
VCS sources MUST enumerate their versioned content rather than infer membership from a physical checkout or repository-cache directory.
Uncommitted, untracked, and administrative files outside that content view MUST NOT enter the selected file set.
A filesystem source MUST treat every file beneath its content root as source content.
Hidden names MUST NOT themselves cause exclusion.

A directory mapping MUST select the complete file set below its path in the backend's content view.
An unavailable or unreadable source root MUST remain a failure.
Native ignore rules and machine-local ignore configuration MUST NOT implicitly alter the selected file set.

## File manifests

A file manifest MUST identify the complete selected file set and each file's content hash at one source state.
A directory mapping MUST remain one entry with one manifest, independently of its number of files.
Manifest children MUST NOT become independently versioned entries.
Every file MUST use the dependency's shared locked revision.

Paths within a directory manifest MUST use canonical `@/` identifiers under Identifiers and Identifier checks in `specs/behavior/paths.md` in `llm-tool-cli`, with the entry root as the identifier root.
Every child identifier MUST be strictly below that root; `@/` MUST NOT identify a directory-manifest child.
Manifest paths MUST retain this canonical spelling, including the `@/` prefix, in persisted records, comparisons, and content hashes.
Manifest paths MUST be unique, and one file path MUST NOT be an ancestor of another file path.
Names MUST NOT undergo Unicode normalization or case folding.
Input models MAY use the shared entity string-normalization defaults before path validation; preservation of surrounding whitespace is not required.
Paths MUST NOT undergo home or glob expansion.
Every listed child MUST be a regular file; directories MUST be inferred from file paths.
An empty directory selection MUST have an empty manifest.

A file entry MUST identify its root file by its own hash; an absent entry MUST identify no current source files.
For comparison, a root file MUST occupy the canonical root position `@/` and directory children MUST occupy their manifest paths.
This comparison convention MUST NOT turn `@/` into a directory-manifest child.

Updating a dependency MUST enumerate the selected revision again rather than freeze the old manifest's membership.
New source files MUST enter the proposed manifest, and removed source files MUST leave it.
Local files MUST NOT be accepted into an installation baseline merely because they exist at a destination.

## Missing files

Whether a source file remains selected MUST be determined by the configured mapping and backend content view under Source file selection.
If a selected file exists at the operation's source revision and its local destination is missing, installation and update MUST install that file normally, regardless of whether the previous manifest contained it.
This requirement MUST apply to standalone file entries and missing children of partially installed directory entries.
Local absence MUST NOT itself cause a file to be skipped or require permission to restore it.

Restoration MUST use the source revision selected by the operation under Operation contracts in `specs/behavior/locking.md`.
Source-integrity and availability checks MUST still pass, and existing modified or unmanaged content MUST retain its normal protection.
Existing objects at file or parent positions MUST undergo the applicable type and preservation checks rather than be treated as missing files.
Unreadable content MUST remain an inspection failure rather than be classified as missing.

Ordinary local inspection MUST continue to compare the actual installed state with the recorded baseline under Local comparison.
Treating a missing file as restorable during installation or update MUST NOT report an incomplete installation as clean or rewrite a baseline to accept the missing content.
Verification of existing content and installation-baseline transitions MUST follow Installation protection and Updating baselines in `specs/behavior/locking.md`.

**Example:** A directory mapping selects `a.txt` and `b.txt` at the operation's source revision.
If local `b.txt` is absent, installation or update MUST restore it after the applicable checks pass, whether it is newly added upstream or was already recorded in the previous manifest.
If the selected source revision no longer contains `b.txt`, installation or update MUST NOT recreate it from an older revision.

## Directories

Directories MUST provide structure for files rather than independently managed descendant content.
Empty directories MUST NOT appear in file manifests or affect content hashes or file-set equality, including for backends that can represent them.
Missing parent directories MUST be created when needed to materialize selected files.
An empty directory selection MUST NOT require creating a destination or reproducing source-only empty directories.

When no managed files require a container, its absence and its existence as a directory MUST be equivalent for local comparison.
A change confined to empty-directory existence MUST NOT by itself require rewriting an installation baseline.
A regular file at an entry root or a required parent position MUST remain observable as file content or a type conflict.
An empty regular file MUST remain different from an empty directory selection or expected absence.
Source-path existence for an initial mapping MUST still follow the `add` contract in `specs/behavior/locking.md`; ignoring empty directories MUST NOT turn an absent source path into an available path.

Placeholder files MUST be ordinary files without special vcsinstall behavior.
Directory permissions and the existence of otherwise empty directories MUST NOT become installation guarantees.

## Content identity

Content identity MUST depend on selected file paths, kinds, and exact bytes.
The same selected content MUST produce the same identity independently of the backend, cache location, and destination name.
VCS revision identifiers MUST NOT substitute for installed-content hashes.
Hashes MUST use SHA-256 with the stored representation in Hash representation in `specs/behavior/lockfile.md`.

Content identity MUST NOT depend on:

- timestamps.
- ownership.
- permissions.
- absolute filesystem locations.
- files outside the applicable comparison or contribution scope.
- empty directories.

### File hashes

A file hash MUST be SHA-256 of its exact bytes.
Hashing MUST NOT normalize line endings, decode text, or apply VCS filters.

### Directory hashes

A directory hash MUST be derived from its complete file manifest, with paths sorted by their literal UTF-8 bytes.
Its SHA-256 input MUST concatenate one record per file, with each record containing these byte sequences in order:

1. `file` encoded as ASCII, followed by one NUL byte.
2. The complete canonical manifest path, including its `@/` prefix, encoded as UTF-8, followed by one NUL byte.
3. The complete file content hash, including its `sha256:` prefix, encoded as ASCII, followed by one NUL byte.

An empty manifest MUST hash an empty byte sequence.
Entry kinds MUST distinguish that digest's directory use from an empty regular file.
Directory enumeration and manifest serialization order MUST NOT affect the hash.
An aggregate hash MUST NOT substitute for the per-file manifest needed to identify ownership.

## Local comparison

Local inspection MUST obtain managed directory paths from the recorded manifest without reading source repositories or repository-cache data.
Target-VCS tracking, staging, and ignore status MUST NOT change recorded ownership or suppress a managed file's modifications.
Files below an entry root that are absent from its manifest MUST remain outside recorded baseline ownership.
Their contents MUST NOT affect ordinary local baseline equality or be read merely to calculate a baseline-scoped content hash.
Publication MUST instead enumerate the complete local directory under Contributions, independently of recorded baseline membership.

Comparison against both an existing baseline and proposed source content MUST use one scope containing the union of their file paths.
A file omitted by either state MUST represent expected absence at that position in that state.
Unmanaged files occupying proposed new source paths MUST participate in collision checks rather than disappear from the comparison.
An entry's root and the parent paths needed to reach scoped files MUST be inspected for type obstructions.
An obstructing regular file MUST remain a type change even when its own path is not a manifest child.
A directory occupying a scoped file position MUST NOT be read recursively as newly managed content.

Equality MUST require agreement at every scoped position, with the structural-directory equivalence in Directories.
Matching some changed files MUST NOT acknowledge remaining managed differences.
Source-integrity checks MUST enumerate the complete selected source file set, so unexpected source files cannot be hidden by comparing only previously recorded paths.
An empty directory and an absent container MAY reproduce the same zero-file content; source availability and initial source-path validation MUST remain separate checks.

## Filesystem effects

Installation, update, removal, and delivery MUST change only the file paths authorized by the applicable operation.
Applying a changed installation manifest MUST preserve unmanaged files, including descendants within the installed destination.
An unmanaged file at a newly selected path MAY become managed when the operation verifies that it already has the selected source content; conflicting content MUST fail before mutation.
Type changes MUST fail before mutation when completing them would destroy content outside the applicable operation's authorized file set.

Deletion MUST remove authorized files individually.
Directories MAY be pruned only when physically empty and within the operation's containment boundary.
Unmanaged descendants MUST NOT become deletable merely because an ancestor is an entry root or would otherwise be pruned.
Successful removal MAY leave the destination directory and its unmanaged files in place.

Deleting installed directory contents during installation, update, or removal MUST require a structurally valid recorded manifest identifying the owned paths.
Permission to bypass content verification MUST NOT grant ownership of unknown descendants.
Without that ownership information, an operation requiring directory-content deletion MUST fail with guidance to retain files instead.

## Contributions

A directory contribution MUST reproduce the complete current local file tree beneath the selected entry root.
All local files MUST participate automatically, including new files absent from the lockfile manifest.
Selecting the directory entry MUST be sufficient; individual child-file listings MUST NOT be required.
The same directory selection MUST apply in every delivery mode.

Target-VCS tracking, staging, and ignore rules MUST NOT implicitly alter contribution membership.
Hidden files and generated files MUST participate in directory contributions.
Unsupported objects in the local tree MUST fail under Failures before delivery effects.

Contribution change detection MUST compare the complete local tree with the recorded baseline.
A new local file MUST constitute an addition even when ordinary baseline-scoped inspection reports the entry as clean.
A recorded file missing locally MUST contribute a deletion when publication is explicitly requested; installation and update MUST retain the restoration behavior in Missing files.
Root-file type changes MUST remain changes within the selected mapping.

Directory delivery MUST reproduce the complete local tree at the mapped source path, including removal of source-only children when the selected mode permits the change.
Directory comparisons with a recipient or contribution base MUST include its complete tree, so additional source files cannot be hidden by the old installation manifest.
The selected mode's baseline and concurrent-change protection MUST still apply to this complete tree.
Source content outside selected mappings MUST be preserved.
An obstruction or type change that cannot preserve that content MUST fail before delivery.

Publication MUST NOT change configuration, the installation manifest, or lockfile bytes.
A later successful update MUST establish newly published files as managed only when they belong to its selected source revision.

## Failures

Unreadable selected or managed files MUST fail rather than be skipped or reported as clean.
Unsupported objects and names that cannot satisfy the encoding or destination-containment contracts MUST fail when they are in the selected or managed scope.
Unmanaged payloads outside that scope MUST NOT be inspected merely to reject their object kinds.
Preservation checks MAY inspect their existence and kinds when necessary to determine whether a proposed effect would destroy them.
Symbolic links MUST NOT be followed in violation of the shared containment contract.
Preparation and partial-effect guarantees MUST follow Preparation and consistency in `specs/behavior/locking.md` and Failures and completed effects in `specs/behavior/vcs-interaction.md` at their respective operation boundaries.

## Example

An installed directory maps source path `@/guides` and has a manifest containing `@/guide.md` and `@/examples/basic.py`.
Running a local tool creates `__pycache__/basic.pyc` and an empty `output` directory; a developer also creates `examples/new.py`.

- Editing `guide.md` MUST produce a managed modification.
- A missing `examples/basic.py` MUST be restored by installation or update when it remains selected, after the operation's protection and source checks pass.
- The cache file, empty directory, and new local file MUST NOT change ordinary managed-content equality.
- Pushing the directory MUST include `examples/new.py` and the cache file automatically.
- Removing the entry MUST remove its verified managed files while preserving the cache file and `examples/new.py`.
- A later update whose source contains `examples/new.py` MUST include it in the proposed manifest and apply the ordinary collision and equality checks before recording ownership.
