# Source and hosting interaction

## Goal of the document

This document describes the common source and hosting contract for identifying repositories and accessing their content.

## Scope

This specification covers shared repository and source-path semantics and the observable guarantees at source and hosting boundaries.
The following are out of scope:

- CLI parsing and result serialization.
- configuration and lockfile schemas.
- installation-baseline transitions and content-hash computation.
- cache layout and access locking.
- Python interfaces and VCS wire protocols.
- local synchronization effects.

## Contract boundaries

Terminology MUST follow `specs/dictionary.md`.
Architectural responsibilities and integration choices MUST follow `specs/architecture/modules_layout.md`.
CLI argument syntax and input decoding MUST follow `specs/behavior/cli.md`; persisted declarations MUST follow `specs/behavior/config.md`.
Revision selection, baseline verification, and project state changes MUST follow `specs/behavior/locking.md`.
Persisted revision and content identities MUST follow `specs/behavior/lockfile.md`.
File membership, manifests, and equality MUST follow Source file selection, File manifests, and Local comparison in `specs/behavior/files-and-directories.md` at source-inspection boundaries.
Source and hosting access using the repository cache MUST follow `specs/behavior/cache.md`.

Test-only adapters MUST be available only when explicitly supplied by a test environment under `specs/architecture/tests.md`.
Normal application execution MUST reject them as unsupported, without a production CLI option that enables them.
They MUST be supplied independently; selecting one MUST NOT implicitly enable another source or hosting adapter.

## Repository locators and identity

Repository identity MUST consist of the source backend and its normalized native repository locator, independently of equivalent input syntax.
Version selectors and resolved revisions MUST NOT form part of repository identity.
Modeled repository locator strings MUST have leading and trailing whitespace, including spaces, stripped before their validated text establishes repository identity.
After this stripping, normalization MUST preserve the locator's native meaning, including transport and authority, under the selected backend's contract.
Different locators MUST NOT be equated merely through matching basenames or assumed equivalence between HTTPS and SSH addresses.

Normalized native locators MUST exclude vcsinstall's source-version fragments.
Native query parameters MUST retain their transport meaning; vcsinstall MUST NOT interpret them as source-version selectors.
`@` in a transport address MUST NOT be interpreted as a vcsinstall version suffix.
Relative filesystem locators MUST use the directory base established by their input contract in `specs/behavior/cli.md` or `specs/behavior/config.md`.

## Repository detection

Detection MUST preserve explicit backend choices and follow the input-specific inference rules in Repository arguments in `specs/behavior/cli.md`.
An explicit backend option MUST bypass detection and leave interpretation of the supplied locator to the selected backend.
An already declared backend MUST NOT be re-inferred when loading configuration or using a lock.
Ambiguous local inputs MAY be recognized through read-only repository metadata, considering specialized backends before ordinary filesystem content.
Backend-specific malformed or unsupported locator values MUST be ordinary non-matches during normalization and probing rather than operational failures.
Normalization MUST distinguish a normalized locator from a non-match; a rejected value MUST NOT become an empty or fabricated locator.
Only a definite non-match MAY continue to the next backend; unreadable metadata or another operational failure MUST fail detection.
Unexpected internal processing failures MUST follow General principles and Exception boundaries in `specs/architecture/errors.md` rather than become non-matches or ordinary diagnostics.
An input not recognized by any supported backend MUST fail rather than create a source declaration.

Local detection MUST preserve the supplied root's meaning and MUST NOT inspect an entire content tree solely to choose a backend.
Remote detection MAY query native transport metadata, but MUST complete within a bounded execution time and retain the native noninteractive authentication contract.
Detection MUST NOT clone repositories, fetch content objects, or create cache data.
Detection and failed probes MUST preserve project declarations, locks, and installed content.
Ownership of common input syntax, native locator interpretation, and probe effects MUST follow Adapter and cache boundaries in `specs/architecture/modules_layout.md`.

## Source paths

A source path MUST be a canonical `@/` identifier anchored at the selected dependency's repository root at the selected revision.
The prefix MUST refer to that repository root independently of the target project's root, the invocation's working directory, or the repository's acquisition location.
`@/` MUST identify the repository root itself.
Source-path strings MUST first apply whitespace stripping under Path-string normalization in `specs/behavior/files-and-directories.md`.
Subsequent lexical normalization MUST follow the Normalization contract in `specs/behavior/paths.md` in `llm-tool-cli`, retaining its canonical result as source-path identity.
Inputs without the `@/` prefix MUST be rejected at boundaries accepting a source-path identifier; CLI source references MUST separate the dependency name from the source-path identifier under Source arguments in `specs/behavior/cli.md`.

Source paths MUST NOT imply hosting-specific package discovery or modify the dependency's repository locator or version selector.
They MUST NOT be interpreted as package names or expanded as globs.
Source paths MUST NOT undergo local filesystem resolution, home expansion, URL or percent decoding, or interpretation relative to the target project.
After modeled path-string stripping, ordinary path-segment text MUST retain its literal meaning under the shared normalization contract.

**Example:** `@/guides/../LICENSE` normalizes to `@/LICENSE`, and `@/.` normalizes to `@/`.

The backend MUST identify its content root, including which repository data belongs to the content tree and which administrative data is excluded.

## Backend selection and capabilities

The supported source backends MUST be:

- `git` — Git repositories, under `specs/behavior/vcs/git.md`.
- `filesystem` — Ordinary filesystem directories, under `specs/behavior/vcs/filesystem.md`.
- `noop` — Controlled repository views, under `specs/behavior/vcs/noop.md`.

Every supported source backend MUST be available through ordinary backend selection without additional registration or opt-in, independently of hosting selection.

Backend selection MUST be explicit in the normalized repository identity under Repository locators and identity.
An explicitly selected unknown or unsupported backend MUST fail without falling back to Git or ordinary filesystem access.
Creating a backend specification MUST NOT by itself advertise an unimplemented command or capability as available.

Each backend MUST define its supported:

- native locators and repository identity.
- default version and explicit selector kinds.
- immutable revision representation and availability limits.
- file and directory extraction semantics.
- repository-cache use.

Source resolution and extraction MUST use an available repository view and MUST NOT perform:

- repository acquisition.
- repository refresh.
- synchronization preparation and effects.

Unsupported transports or selectors MUST fail explicitly rather than be ignored or reinterpreted as another capability.
Unsupported command options MUST be rejected under the CLI's invalid-argument contract before repository acquisition, source-content inspection, or external mutation; backend-specific compatibility checks MAY follow the metadata probes needed to identify that backend.
Read access MUST NOT imply permission to publish.
Ordinary filesystem and HTTP source notation MUST NOT by itself imply VCS revision history.

Additional VCS support MUST preserve native semantics rather than impose Git's branch model on every backend.
For example, Mercurial may support tags and changeset identifiers, while SVN revisions identify repository states and branch or tag locations are ordinary source paths.
Corresponding locator forms can include `hg+https://example.com/shared#tag=v1.0`, `hg+file:///shared#rev=CHANGESET_HASH`, or `svn+https://example.com/shared#rev=1234` when those backends are supported.
SVN support MUST NOT assume a `trunk`, `branches`, or `tags` layout or invent native named branch selectors.
Additional selector kinds, such as Mercurial bookmarks, MUST have explicit input and result semantics before being exposed.

## Hosting selection and capabilities

Hosting MUST provide repository acquisition and refresh independently of local source inspection.
Git dependencies MUST use native Git acquisition, including repositories hosted on GitHub or accessed through SSH aliases.
Filesystem dependencies MUST use direct access to the declared directory.
Controlled noop hosting MAY be supplied explicitly by tests independently of the source adapter.
Production selection MUST remain owned by the hosting boundary and MUST NOT invoke hosted-service discovery or authentication.
Selection MUST preserve repository identity, native locator, selector meaning, and cache identity.
Each hosting implementation MUST define its acquisition and refresh guarantees under its corresponding contract in `specs/behavior/hosting/`.

## Native client execution

Client selection and protocol integration MUST follow External integrations in `specs/architecture/modules_layout.md`.
A required but unavailable client MUST cause an operational failure; vcsinstall MUST NOT install it automatically.
Authentication and commit-identity failures MUST remain operational diagnostics.
Filesystem and noop source or hosting access MUST NOT require or invoke a VCS executable.

Client invocations MUST use locator syntax supported by the selected installed client, preserving locator meaning under Repository locators and identity.
Repository locators, selectors, and paths MUST be passed as data without allowing them to become shell commands or unintended client options.
After applicable modeled path-string stripping, paths MUST retain their literal meaning even when a native client ordinarily interprets them as patterns or revision syntax.
Native client output MUST follow the CLI's protocol-delivery contract rather than be copied directly into the result stream.

Source inspection MUST NOT alter an original repository's working files or administrative state.
Cache changes MUST remain separate from original source and target project state.
Repository acquisition and source inspection MUST NOT mutate the original source repository.

## Repository acquisition and refresh

Acquisition MUST make repository data and version metadata available for source resolution and extraction under the requested operation's revision and freshness requirements in `specs/behavior/locking.md`.
Acquired repository data MUST correspond to the declared backend and normalized repository locator; data for a different identity MUST NOT satisfy the request.
Configurations where an acquired repository location and an installation destination overlap are outside the supported use cases.
Available version metadata MUST distinguish observed branch and tag targets from the source's advertised default branch where applicable.
Acquisition or refresh MUST NOT itself choose a new installation revision or change project declarations, lock state, or installed content.

Valid available data MAY be reused when it satisfies the operation's requirements.
When current source information is required, refresh MUST obtain the required observations from the declared source; failed refresh MUST NOT be satisfied by stale cached references.
Acquisition of a locked immutable revision MUST NOT require adopting the current branch or default revision.
Missing required objects or metadata MUST be acquired through hosting or reported as an operational failure; source inspection MUST NOT trigger implicit transport access.
An available view MUST NOT imply complete history, retained snapshots, or future availability of every revision.

Cached acquisition and reuse MUST follow `specs/behavior/cache.md`.
An uncached hosting implementation MUST define how the declared source becomes available without requiring the cache.
Acquisition MUST preserve the original source's content and administrative state.

## Version resolution and anchoring

A successful resolution MUST identify one complete immutable revision for the selected repository state.
The identifier MUST keep the same content meaning across invocations and cache locations; it MUST NOT depend on a temporary checkout path or invocation timestamp.
An identifier MUST NOT imply that historical content remains available forever.

Named selectors MUST remain distinct from their resolved immutable revisions.
Default-version inference and selector recording MUST follow Version selection in `specs/behavior/locking.md` and the backend's own contract.
An unversioned source MAY define its default as the current live content state; it MUST NOT invent a branch or tag to represent that state.
Bare-selector ambiguity MUST fail under the CLI contract rather than use a client's shorthand precedence.
Resolution MUST convert an unresolved input selector to a supported branch, tag, or immutable revision selection before producing a resolved source.

Resolution MUST interpret the available view and supplied version metadata; current-source checks MUST use observations satisfying Source freshness in `specs/behavior/locking.md`.
Source content prepared for one dependency operation MUST belong to the revision selected for that operation under State and shared revisions in that specification.
Source and recipient state stability within an invocation MUST follow General behavior in `specs/behavior/cli.md`.

## Source inspection and extraction

Inspection and extraction MUST use the requested immutable revision from the available view or fail operationally; they MUST NOT silently substitute a newer, older, or default revision.
Cached repository views MUST follow Repository reuse and revision isolation in `specs/behavior/cache.md`.
An uncached backend MUST define how a requested revision is verified against available source content without requiring the cache.

Extraction MUST distinguish:

- a regular file with its exact bytes.
- a directory with its complete selected file set under Source file selection and Directories in `specs/behavior/files-and-directories.md`.
- an absent path at an otherwise available revision.
- an unavailable revision or failed inspection.

An inspection or retrieval failure MUST NOT be reported as source-path absence.
Whether absence can establish or update an entry MUST follow `specs/behavior/locking.md`.
Extracted file-path metadata MUST adopt whitespace stripping under Path-string normalization in `specs/behavior/files-and-directories.md`.
After that stripping, extraction MUST preserve literal names and file bytes without applying package discovery or application-specific layout rules.

Unsupported source objects or names that cannot be represented safely at the destination MUST fail before project mutation rather than be omitted, followed, or renamed beyond the specified path-string normalization.
Symbolic links MUST NOT bypass source or destination containment.
Prepared source content MUST satisfy Content identity in `specs/behavior/files-and-directories.md` and Source integrity and cache independence in `specs/behavior/locking.md`.

## Failures

Acquisition, refresh, and source-inspection failures MUST preserve their native diagnostics and MUST NOT silently select another repository or revision.
Expected failures MUST follow `specs/architecture/errors.md`.
Local synchronization MUST follow its separate operation contract in `specs/behavior/locking.md`.
