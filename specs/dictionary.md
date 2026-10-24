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

- `source repository` — a version-control repository from which files are installed.
- `target repository` — the repository receiving installed files.
- `project configuration file` — `vcsinstall.toml`, the tool's configuration for a project.
- `lockfile` — `vcsinstall.lock`, a record of the source revisions and installed files used to reproduce an installation.
- `push back` — transfer of local changes to installed files back to their source repository.
- `protocol` — a CLI output contract selected by `--protocol`.
- `human protocol` — output protocol optimized for terminal users.
- `llm protocol` — output protocol optimized for coding agents invoking the tool.
- `automation protocol` — output protocol optimized for programs; output is serialized as JSON Lines.
- `expected error` — an operational failure represented by an environment-error model and returned through a result, both provided by `llm_tool_cli`.
- `warning` — a non-fatal problem discovered while processing a request.

Installation, lockfile, and push-back terms describe the intended product scope; they do not prescribe command syntax or storage formats.
