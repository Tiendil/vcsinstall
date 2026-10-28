# Dictionary

## Goal of the document

This document defines terms specific to `vcsinstall` that are shared by multiple specifications.

## Scope

The scope of this specification is limited to project-specific terminology.

The following topics are out of scope:

- detailed behavior.
- implementation requirements.
- configuration schemas.

## Terms

- `dependency` — a named installation unit binding one source repository and requested version selector to entries that share one locked immutable revision.
- `effective selector` — the source version selection used by an operation, including an inferred branch retained in a matching dependency lock.
- `entry` — a mapping from a path within a dependency's source repository to a destination in the target project; each entry belongs to one dependency.
- `installation baseline` — the recorded content state of an entry at its dependency's shared locked revision.
- `file manifest` — the recorded set of selected files within a directory entry, with their literal relative paths and content hashes at one source state.
- `managed file` — a file identified by an entry's recorded installation baseline.
- `unmanaged content` — content in the target project outside the recorded file set, including additional files beneath a directory entry.
- `source backend` — the source kind defining repository identity, version semantics, revision resolution, and content extraction from an available repository view.
- `repository view` — repository data and version metadata available for source inspection, using cached VCS data, a live directory, or controlled test fixtures.
- `hosting adapter` — an integration providing repository acquisition, refresh, and supported change-delivery workflows for a compatible source backend; it may use native repository facilities or a hosting service.
- `native repository locator` — the backend-native location or identifier for a source repository, without vcsinstall's version fragment.
- `source repository` — the source content root accessed through a selected backend, including a VCS repository or an ordinary filesystem directory.
- `source path` — a canonical `@/` identifier selecting content within a dependency's source repository.
- `dependency source reference` — a CLI `NAME@/PATH` reference combining a declared dependency name with a source path anchored at that dependency's repository root.
- `publication repository` — the repository selected to receive a contribution, independently of the source used for installation and updates.
- `review target` — the source repository and branch into which a hosted contribution requests integration.
- `delivery mode` — the explicitly requested way to deliver or apply selected local changes.
- `target repository` — the repository receiving installed files.
- `project configuration file` — `vcsinstall.toml`, the tool's configuration for a project.
- `lockfile` — `vcsinstall.lock`, a record of shared dependency revisions, per-entry content identities, and directory file manifests used to reproduce an installation.
- `push back` — transfer of local changes to installed files back to their source repository.
- `protocol` — a CLI output contract selected by `--protocol`.
- `human protocol` — output protocol optimized for terminal users.
- `llm protocol` — output protocol optimized for coding agents invoking the tool.
- `automation protocol` — output protocol optimized for programs; output is serialized as JSON Lines.
- `expected error` — an operational failure handled through the project's diagnostic contract in `specs/architecture/errors.md`.
- `warning` — a non-fatal problem discovered while processing a request.

Installation, lockfile, and push-back terms describe the intended product scope; they do not prescribe command syntax or storage formats.
