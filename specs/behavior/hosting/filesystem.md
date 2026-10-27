# Filesystem hosting

## Goal of the document

This document describes access to an ordinary filesystem source and explicitly authorized delivery of local changes into it.

## Scope

This specification covers directory acquisition, the overwrite recipient, selected source writes, verification, and partial failures.
The following are out of scope:

- source selectors, revision availability, and extraction.
- VCS branches, commits, and history.
- project declarations, installation-baseline transitions, and persisted formats.
- filesystem transaction or synchronization mechanisms.

## Availability and source access

The hosting implementation name MUST be `filesystem`, compatible with source backend `filesystem`.
The common hosting contract MUST follow `specs/behavior/vcs-interaction.md`.
The source and overwrite recipient MUST be the directory identified under `specs/behavior/vcs/filesystem.md`.
Acquisition and delivery MUST use ordinary filesystem access without a VCS or hosted-service client.
It MUST NOT populate or require the repository cache, acquire its lock, or depend on `--cache-dir`.
Temporary preparation MUST NOT imply retained source history.

## Directory acquisition

Acquisition MUST expose the existing readable source directory as a live repository view without creating it or copying it into the repository cache.
A missing directory, unreadable location, or non-directory source root MUST cause an operational failure.
Acquisition MUST NOT create source history, invent version metadata, or change directory content.
Access to this view MUST NOT require overwrite authorization or source write permission.
Live-state fingerprinting, version availability, and extraction MUST follow `specs/behavior/vcs/filesystem.md`.
Providing a live view MUST NOT imply an immutable retained snapshot; source changes preventing consistent inspection MUST fail under that source contract.

## Overwrite delivery

Filesystem hosting MUST support only delivery mode `overwrite`.
The required `--mode overwrite` MUST explicitly authorize applying the selected changed entries directly to their recorded source paths under Direct source overwrite in `specs/behavior/vcs-interaction.md`.
Other modes MUST fail as unsupported under the CLI's invalid-argument contract before payload reads or source access.
This mode MUST support selected file and directory changes of these kinds:

- additions.
- replacements.
- type changes.
- deletions.

It MUST NOT create a branch, commit, or source-side history record.

The live source MAY differ from the installation's locked fingerprint; selecting overwrite mode MUST authorize replacing the selected source mappings despite those differences.
A selected directory MUST be replaced as a complete tree, including removal of source-only children within that selected tree.
Existing source content outside the selected mappings MUST remain unchanged by the invocation.
Missing parent directories MAY be created where required to apply a selected mapping, subject to source containment.
Selected edits proposing conflicting content at the same source path MUST fail before any source write.

The source root itself MUST remain a directory; a mapping of `.` MUST NOT authorize deleting that root or replacing it with a file.
Matching locks, trustworthy entry baselines, and local-state inspection MUST remain required under the common publication contract.
Overwrite authorization MUST NOT bypass containment, permissions, or unsupported-object failures.
Validation and preparation for every selected change MUST complete before the first source write.
Failure after writes begin MUST report partial source effects under Failures and completed effects in `specs/behavior/vcs-interaction.md`.

Mode-specific options MUST follow the applicability rules in `specs/behavior/cli.md`.
Publication-repository overrides MUST NOT redirect these writes to another directory; the source declaration MUST remain the recipient.
Successful writes MUST be verified against the selected local content and identify the resulting complete source fingerprint.
Publication records MUST use null `branch` and that fingerprint as `push_revision`, while `revision` MUST retain the unchanged installation baseline.
Configuration, lockfile bytes, and installed content MUST remain unchanged; a later dependency update MUST acknowledge equal source content through the ordinary baseline-update rules.

## Example

A dependency `local-guides` refers to `file:///shared/guides` without a selector and has locked fingerprint A.

1. The source directory changes to fingerprint B. `dependency outdated local-guides` reports A and B even if only an uninstalled source file changed.
2. Restoring a missing installation with `install --locked` fails when it needs content at A, because the live source is B and no history is retained.
3. `dependency update local-guides` can install B using the recorded entry baselines for local-change protection.
4. After a local guide edit, `vcsinstall dependency push local-guides --mode overwrite` overwrites that guide in the source and reports fingerprint C. The lock remains at B.
5. The next dependency update records C without rewriting local content already equal to the source.

Fingerprints A, B, and C in this example stand for complete `sha256:` identifiers, not literal accepted revision values.
