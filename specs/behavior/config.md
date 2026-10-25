# Configuration

## Goal of the document

This document describes vcsinstall's project configuration contract, including dependency declarations and entry mappings.

## Scope

This specification covers configuration-file behavior and the declarative project schema.
The following are out of scope:

- CLI invocation and output.
- lockfile schemas and content-hash algorithms.
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
source_path = "specs"
destination = "@/specs/shared"

[[entries]]
dependency = "formatting"
source_path = "LICENSE"
destination = "@/licenses/shared.txt"
```

## Dependencies

Each key in `dependencies` MUST be the dependency's project-local name under the Named dependencies and shared locks contract in `specs/behavior/cli.md`.
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

Backend support and effective repository identity MUST follow the Backend support and native semantics and Repository locators and identity contracts in `specs/behavior/cli.md`.
The explicit `vcs` field MUST select the backend for `repository`; a transport such as HTTPS MUST NOT override it.
Native transport, authority, and query semantics MUST be preserved.

CLI-generated declarations MUST record the selected backend and normalized native repository locator separately.
VCS-qualified CLI URLs and source-version fragments MUST be decomposed into the corresponding configuration fields rather than retained as a combined source argument.
Native VCS schemes, such as `svn+ssh://` for SVN, MUST retain their native meaning.

Relative filesystem repository locators in configuration MUST resolve against the project root, independently of the invocation's working directory.
Repository locations MAY be outside the target project; destination containment MUST NOT be imposed on source repository locations.
When a CLI repository argument was relative to the invocation's working directory, the recorded locator MUST retain the same repository identity when later read from the configuration's project root.

### Version selection

At most one of `branch`, `tag`, and `rev` MUST be present.
When all three are absent, the dependency MUST follow the source repository's default version under the CLI source-version contract.
A default selector MUST remain distinct from an explicitly named branch, even when both currently resolve to the same revision.

Branch and tag values MUST retain their literal spelling and case.
They MUST select the same named branch or tag rather than a semantic-version range or the newest release.
`rev` MUST identify an immutable revision in the backend's complete representation; for Git it MUST be a full commit hash.
CLI revision abbreviations MUST be resolved before being written as `rev`.
Selector fields MUST be supported by the selected backend under the CLI native-semantics contract.

CLI bare-reference inference MUST be recorded using the resolved selector field rather than repeated on later commands.
Configuration selector strings MUST NOT undergo URL-fragment or percent decoding.
Resolving branch and tag targets and checking source availability MUST belong to commands requiring that source information rather than configuration loading.

## Entries

Each item in `entries` MUST be a table containing these required nonempty string fields:

- `dependency` — The exact name of one declared dependency.
- `source_path` — The literal path of a file or directory within that dependency's repository.
- `destination` — The final local path of the installed entry.

Each entry MUST refer to an existing dependency declaration.
Repository and version selection MUST come from that declaration under the CLI shared-lock contract.
The CLI's `dependency://NAME/PATH` source argument MUST be represented by separate `dependency` and `source_path` fields.
Configuration order MUST NOT alter dependency membership or entry identity.

### Source paths

`source_path` MUST follow the Source paths contract in `specs/behavior/cli.md`.
It MUST be repository-relative, use `/` separators, and remain within the repository content root.
`.` MUST select the repository's content root.
Paths MUST remain literal rather than undergo glob expansion, local home expansion, or project-path interpretation.
Configuration source-path strings MUST NOT undergo URL or percent decoding.
The same source path MAY appear in multiple entries with different destinations.

### Destinations and entry identity

Configured destinations MUST follow the Mixed path normalization contract in `specs/behavior/paths.md` in `llm-tool-cli`, with the project root as the base for relative filesystem inputs.
Their meaning MUST remain independent of the invocation's working directory.
They MUST resolve strictly below the project root under the shared containment contract, including its symbolic-link rules, without requiring an existing target.

New or changed entry declarations written by vcsinstall MUST store `destination` as a canonical `@/` identifier.
That identifier MUST remain the entry's CLI identity under the Local path arguments and entry selection contract in `specs/behavior/cli.md`.
Source-path names and destination names MAY differ under that contract.

Every canonical destination MUST be unique across all entries, including entries belonging to different dependencies.
Repeated mappings MUST fail validation even when their other fields are identical.
Overlapping destinations and conflicts with the selected configuration or project lockfile MUST fail under the CLI destination rules.

## Configuration and locked state

Configuration MUST describe requested dependencies and entry mappings.
Resolved branch or tag commits and installed-content hashes MUST remain installation state under the CLI lockfile contract.
A `rev` selector MUST represent the requested immutable pin rather than evidence that an entry has been installed or verified.

A valid configuration MAY have no matching lock or no installed content.
Editing a dependency definition or entry mapping MUST NOT make an old lock match the new declaration or acknowledge local changes as an accepted baseline.
Lock matching, source freshness, and protection of prior installation baselines MUST follow the CLI command contracts.

## Configuration editing

Commands MUST change configuration only when authorized by their contracts in `specs/behavior/cli.md`.
Successful edits MUST leave valid configuration and preserve unrelated declarations and their meanings.
Edits SHOULD preserve existing comments and declaration order.
The effects of replacing or removing declarations, including failure guarantees, MUST follow those command contracts.

New or changed dependency declarations MUST use the separate normalized repository and selector fields defined above.
New or changed entry declarations MUST use the separate dependency, source-path, and canonical-destination fields defined above.

## Starter configuration

Init MUST select and create its target under the shared initialization contract, using default filename `vcsinstall.toml` and the CLI's invocation cwd and optional `--config`.
The created file MUST pass vcsinstall configuration validation before success is reported.
Created files MUST remain after subsequent load or validation failure under the shared project-scope contract.

The starter MUST contain active schema version `1` and no active dependency declarations or entry mappings.
It MUST include a commented example of a named dependency and an entry referring to it, using this schema:

```toml
version = 1

# [dependencies.shared]
# vcs = "git"
# repository = "https://example.com/team/shared.git"
# branch = "main"

# [[entries]]
# dependency = "shared"
# source_path = "specs"
# destination = "@/specs/shared"
```

## Validation and diagnostics

Invalid configuration MUST fail as a whole rather than omit invalid declarations or treat malformed content as an empty project.
Shared configuration diagnostics MUST retain their native information under `specs/behavior/config.md` in `llm-tool-cli`.
Declaration-validation failures MUST identify the affected dependency or entry and the invalid field or conflicting mapping.
Shared destination-normalization and containment failures MUST preserve the diagnostics defined in `specs/behavior/paths.md` in `llm-tool-cli`.
