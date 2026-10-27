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
- `source backend` — the source kind defining repository identity, version semantics, revision resolution, and content extraction from an available repository view.
- `repository view` — repository data and version metadata available for source inspection, using cached VCS data, a live directory, or controlled test fixtures.
- `hosting adapter` — an integration providing repository acquisition, refresh, and supported change-delivery workflows for a compatible source backend; it may use native repository facilities or a hosting service.
- `native repository locator` — the backend-native location or identifier for a source repository, without vcsinstall's version fragment.
- `source repository` — the source content root accessed through a selected backend, including a VCS repository or an ordinary filesystem directory.
- `publication repository` — the repository selected to receive a contribution, independently of the source used for installation and updates.
- `review target` — the source repository and branch into which a hosted contribution requests integration.
- `delivery mode` — the explicitly requested way to deliver or apply selected local changes.
- `target repository` — the repository receiving installed files.
- `project configuration file` — `vcsinstall.toml`, the tool's configuration for a project.
- `lockfile` — `vcsinstall.lock`, a record of shared dependency revisions and per-entry installed-content hashes used to reproduce an installation.
- `push back` — transfer of local changes to installed files back to their source repository.
- `protocol` — a CLI output contract selected by `--protocol`.
- `human protocol` — output protocol optimized for terminal users.
- `llm protocol` — output protocol optimized for coding agents invoking the tool.
- `automation protocol` — output protocol optimized for programs; output is serialized as JSON Lines.
- `expected error` — an operational failure represented by an environment-error model and returned through a result, both provided by `llm_tool_cli`.
- `warning` — a non-fatal problem discovered while processing a request.

Installation, lockfile, and push-back terms describe the intended product scope; they do not prescribe command syntax or storage formats.
