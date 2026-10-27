# Filesystem sources

## Goal of the document

This document describes ordinary filesystem directories as uncached sources with content-based revision anchors.

## Scope

This specification covers source identity, live-content interpretation, revision availability, and extraction from an available directory view.
The following are out of scope:

- directory acquisition, outgoing changes, and VCS history.
- repository-cache snapshots or historical retention.
- shared CLI parsing and persisted schemas.
- content-hash algorithms and installation-baseline transitions.
- filesystem transaction or synchronization mechanisms.

## Backend and source location

The backend name MUST be `filesystem`.
The common contract MUST follow `specs/behavior/vcs-interaction.md`.
Acquisition of the source directory and outgoing changes MUST follow `specs/behavior/hosting/filesystem.md`.
The supplied repository view MUST expose the existing readable source directory for local inspection.
An unavailable or invalid directory view MUST remain an operational failure rather than be treated as an empty repository.

Backend selection for plain `file://` repository URLs MUST follow Repository locators and identity in `specs/behavior/cli.md`.
File URLs MUST support an empty authority or `localhost`, decode their filesystem path exactly once, and preserve literal `+` characters.
Other authorities MUST fail explicitly as unsupported locators; mounted filesystem locations MAY be addressed through their native paths.
Query components MUST fail as unsupported rather than be interpreted as filesystem path data.
Native directory paths MUST also be accepted with explicit `--vcs filesystem` or configured `vcs = "filesystem"`.
The [file URI specification](https://www.rfc-editor.org/rfc/rfc8089.html) defines the local authority and path notation used here.

The normalized native repository locator MUST be the absolute source directory path, with root resolution following Project-root resolution in `specs/behavior/paths.md` in `llm-tool-cli` after applicable home expansion and URI decoding.
Relative locators MUST use the respective bases established by `specs/behavior/cli.md` and `specs/behavior/config.md`.
Equivalent file-URL and native-path forms selecting the same normalized root MUST have the same repository identity.

## Live revisions and selectors

The source directory MUST have one current content state rather than an invented branch or tag history.
Its immutable revision identifier MUST be the complete directory content hash of the source root under Content identity in `specs/behavior/locking.md`, including the `sha256:` representation from `specs/behavior/lockfile.md`.
This fingerprint MUST cover the complete source tree, not only the declared entries.

An omitted selector MUST mean the directory's current live state.
Configuration and lock records MUST retain omitted selector fields; locking MUST record the resolved fingerprint in `revision` without inventing a named selector or turning live tracking into a revision pin.
CLI selector results MUST use `ref_kind = default` and null `ref` for this selection.

An explicit `rev` MUST be a complete fingerprint in the representation above and MUST select exactly that content state.
Abbreviations MUST NOT be accepted.
Branch and tag selectors MUST fail as unsupported before source access.
Bare-reference inference MAY recognize a complete revision fingerprint but MUST NOT invent another selector kind.

Freshness checks for live selection MUST inspect the current complete source fingerprint under Source freshness in `specs/behavior/locking.md`.
A change outside the installed entry paths MUST still make the dependency outdated when it changes that fingerprint.
Immutable-pin freshness MUST follow Source freshness in `specs/behavior/locking.md`.

## Revision availability and extraction

The backend MUST NOT populate or require the repository cache, acquire its lock, or depend on `--cache-dir`.
Source preparation MAY use temporary storage for the current invocation without retaining repository snapshots for later operations.
Cache cleanup MUST NOT change which filesystem revisions are available.

An operation requiring content at a locked or pinned revision MUST verify that the live source's complete fingerprint matches that revision.
If it differs, the requested revision MUST be reported as unavailable; matching content at some selected paths MUST NOT substitute for the complete requested source state.
The backend MUST NOT reconstruct missing source history from installed files or silently adopt the live fingerprint as an existing lock.

All content prepared for one dependency operation MUST agree with its one selected fingerprint.
Source changes that prevent consistent preparation MUST cause operational failure before project mutation.
Unreadable or unsupported objects MUST fail rather than be skipped while fingerprinting or extracting.

Source `.` MUST select the source directory's contents, including empty directories and hidden content.
Files and directories MUST be treated as ordinary content without VCS ignore rules or special treatment of files named after VCS metadata.
Symbolic links below the resolved source root MUST be rejected under Filesystem containment in `specs/behavior/paths.md` in `llm-tool-cli`; the root itself MAY be selected through a resolved symlink.
Source and installed destination locations MUST NOT overlap in either direction; operations creating such an overlap MUST fail before mutation.

Installation and updates MUST follow the ordinary protection and equality rules in `specs/behavior/locking.md`.
An update using live selection MUST prepare the current source state without requiring the old source fingerprint to remain retrievable when trustworthy entry baselines already exist.
Operations that do not need source content, including local inspection and an already satisfied installation, MUST NOT require the old source state to remain available.
