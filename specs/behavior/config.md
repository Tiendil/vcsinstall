# Configuration

## Goal of the document

This document describes vcsinstall's project configuration contract, including dependency declarations and entry mappings.

## Scope

This specification covers configuration-file behavior and the declarative project schema.
The following are out of scope:

- CLI invocation and output.
- revision locking, lockfile schemas, and content-hash algorithms.
- source fetching and installation algorithms.
- cache and checkout organization.
- module ownership and Python interfaces.

## Configuration file discovery

vcsinstall MUST follow the contract in `specs/behavior/config.md` in `llm-tool-cli`, with default filename `vcsinstall.toml`, for:

- discovery.
- resolution.
- loading.
- initialization.
- project scopes.

The selected configuration file's parent MUST be the project root.
Command applicability and `--config` behavior MUST follow `specs/behavior/cli.md`.
Configuration failures MUST preserve the shared contract's diagnostic information.

## Active configuration

Configuration-dependent commands MUST validate the complete selected configuration before performing their requested work.
The selected configuration file and project root MUST remain consistent through execution and output.
Configuration-independent commands MUST NOT load or require project configuration.

Loading and validation MUST NOT fetch sources, require a VCS executable, or inspect installed file contents.
Configuration validity MUST NOT depend on the existence of a lockfile, repository cache, or source path in a remote revision.
Local destination resolution MUST enforce the shared path-containment rules without requiring installed targets to exist.
Loading MUST NOT rewrite the configuration to materialize defaults or normalized values.

## TOML structure

Configuration MUST be UTF-8 TOML under the shared loading contract.
The supported top-level fields MUST be:

- `version` — Required integer schema version.
- `dependencies` — Optional table of named dependency declarations, defaulting empty.
- `entries` — Optional array of entry mappings, defaulting empty.

Configuration tables MUST accept only the fields defined for them in this specification.
The supported schema version MUST be `1`; version values and diagnostics MUST follow the Version contract in `specs/behavior/config.md` in `llm-tool-cli`.
A missing `version`, including in an empty document, MUST cause a schema-validation failure.
A file containing only `version = 1` MUST describe a valid project with no dependencies or entries.
Literal path and selector text MUST be preserved except for the normalization explicitly required by its owning contract.

```toml
version = 1

[dependencies.ci]
vcs = "git"
repository = "https://example.com/team/shared.git"
branch = "main"

[dependencies.formatting]
vcs = "git"
repository = "https://example.com/team/shared.git"
tag = "v1.0"

[[entries]]
dependency = "ci"
source_path = "@/specs"
destination = "@/specs/shared"

[[entries]]
dependency = "formatting"
source_path = "@/LICENSE"
destination = "@/licenses/shared.txt"
```

## Dependencies

Each key in `dependencies` MUST be the dependency's project-local name, matching `[a-z][a-z0-9_-]*`.
Each value MUST be a table with these fields:

- `vcs` — Required nonempty string identifying the source backend.
- `repository` — Required nonempty string containing the native repository locator for that backend.
- `branch` — Optional nonempty string selecting a named branch.
- `tag` — Optional nonempty string selecting a named tag.
- `rev` — Optional nonempty string selecting an immutable source revision.

Dependency names MUST be unique and MUST NOT be inferred from repository basenames.
Different names MAY declare the same repository, including different selectors or independently maintained instances of the same selector.
Equal repository and selector values MUST NOT merge dependency declarations.
Dependencies without entries MUST remain valid declarations.

### Repository identity

Backend support MUST follow Backend selection and capabilities in `specs/behavior/vcs-interaction.md`.
Effective repository identity MUST follow Repository locators and identity in `specs/behavior/vcs-interaction.md` and the selected backend's contract.
The explicit `vcs` field MUST select the backend for `repository`; a transport such as HTTPS MUST NOT override it.

New or changed dependency declarations written by vcsinstall MUST record the selected backend and normalized native repository locator separately.
Hosting implementation and publication-repository selections for change delivery MUST remain invocation-local under `specs/behavior/cli.md` rather than overwrite these source fields or add publication history.
VCS-qualified CLI URLs and source-version fragments MUST be decomposed into the corresponding configuration fields rather than retained as a combined source argument.

Relative filesystem repository locators in configuration MUST resolve against the project root, independently of the invocation's working directory.
Repository locations MAY be outside the target project; destination containment MUST NOT be imposed on source repository locations.
When a CLI repository argument was relative to the invocation's working directory, the recorded locator MUST retain the same repository identity when later read from the configuration's project root.

### Version selection

At most one of `branch`, `tag`, and `rev` MUST be present.
Selector meaning, omitted-selector inference, and reuse of inferred branches MUST follow Version selection in `specs/behavior/locking.md`.

`rev` MUST identify an immutable revision in the backend's complete representation under its source contract in `specs/behavior/vcs-interaction.md`.
Selector fields MUST be supported by the selected backend under Version resolution and anchoring in `specs/behavior/vcs-interaction.md`.

Configuration selector strings MUST NOT undergo URL-fragment or percent decoding.

## Entries

Each item in `entries` MUST be a table containing these required nonempty string fields:

- `dependency` — The exact name of one declared dependency.
- `source_path` — The repository-anchored identifier of a file or directory within that dependency's repository.
- `destination` — The final local path of the installed entry.

Each entry MUST refer to an existing dependency declaration.
Repository and version selection MUST follow State and shared revisions in `specs/behavior/locking.md`.
File selection, directory semantics, and manifest ownership MUST follow Source file selection, File manifests, and Directories in `specs/behavior/files-and-directories.md`.
The CLI's `NAME@/PATH` source reference MUST be represented by separate `dependency` and `source_path` fields.
Configuration order MUST NOT alter dependency membership or entry identity.

### Source paths

`source_path` MUST follow Source paths in `specs/behavior/vcs-interaction.md`.
New or changed entry declarations written by vcsinstall MUST store `source_path` in its canonical form.
Configuration source-path strings MUST NOT undergo URL or percent decoding.
The same source path MAY appear in multiple entries with different destinations.

**Example:** With `dependency = "ci"`, `source_path = "@/specs"` selects content in that dependency, while `destination = "@/docs/shared"` identifies a path in the target project.

### Destinations and entry identity

Configured destinations MUST follow the Mixed path normalization contract in `specs/behavior/paths.md` in `llm-tool-cli`, with the project root as the base for relative filesystem inputs.
Their meaning MUST remain independent of the invocation's working directory.
They MUST resolve strictly below the project root under the shared containment contract, including its symbolic-link rules, without requiring an existing target.

New or changed entry declarations written by vcsinstall MUST store `destination` as a canonical `@/` identifier.
The canonical destination MUST identify the entry at the CLI boundary under Local path arguments and entry selection in `specs/behavior/cli.md`.

Every canonical destination MUST be unique across all entries, including entries belonging to different dependencies.
Repeated mappings MUST fail validation even when their other fields are identical.
Destinations MUST NOT overlap one another or conflict with the selected configuration or project lockfile.

## Configuration and locked state

Separation of declarations from installation state, declaration matching, and prior-baseline protection MUST follow State and shared revisions and Matching configuration in `specs/behavior/locking.md`.
Persisted installation state MUST follow the representation in `specs/behavior/lockfile.md`.

## Configuration editing

Commands MUST change configuration only when authorized by their contracts in `specs/behavior/cli.md`.
Successful edits MUST leave valid configuration and preserve unrelated declarations and their meanings.
Edits SHOULD preserve existing comments and declaration order.
The state effects of replacing or removing declarations and their failure guarantees MUST follow Operation contracts and Preparation and consistency in `specs/behavior/locking.md`.

## Starter configuration

The starter created by `init` under `specs/behavior/cli.md` MUST contain active schema version `1` and no active dependency declarations or entry mappings.
It MUST include a commented example of a named dependency and an entry referring to it, using this schema:

```toml
version = 1

# [dependencies.shared]
# vcs = "git"
# repository = "https://example.com/team/shared.git"
# branch = "main"

# [[entries]]
# dependency = "shared"
# source_path = "@/specs"
# destination = "@/specs/shared"
```

## Validation and diagnostics

Invalid configuration MUST fail as a whole rather than omit invalid declarations or treat malformed content as an empty project.
Declaration-validation failures MUST identify the affected dependency or entry and the invalid field or conflicting mapping.
Shared destination-normalization and containment failures MUST preserve the diagnostics defined in `specs/behavior/paths.md` in `llm-tool-cli`.
