# Module structure

## Goal of the document

This document describes module ownership and public boundaries.

## Scope

This specification covers project modules and responsibilities.
The following are out of scope:

- implementation design.
- runtime behavior.
- entity/error/test conventions beyond placement.
- future installation and repository-management architecture.

## Dictionary

- `module` — a Python package/module owning coherent functionality.
- `submodule` — a narrower responsibility within a module.
- `test submodule` — tests of a parent module/submodule.

## Modules

All Vcsinstall code MUST belong under `vcsinstall/`.

- `cli` — Command parsing, shared command registration, and application initialization.
- `skills` — Document definitions and packaged Markdown.

The executable and `python -m vcsinstall` MUST use the same CLI entry point.
The package root MUST NOT duplicate distribution version metadata.
Modules MUST have current responsibilities; empty placeholder subsystems MUST NOT be added for future functionality.

## Shared infrastructure

Vcsinstall MUST use application construction and shared skill/version command registration from `llm_tool_cli`, supplying its document definitions and distribution identity `vcsinstall`.
Static application settings MUST be initialized before command-line parsing, using the tool label `VCSINSTALL`.
Static settings MUST supply `vcsinstall.toml` as the default project configuration filename before parsing.

The following MUST remain owned by `llm_tool_cli` rather than local wrappers:

- global CLI options.
- protocol values and output cells.
- rendering, buffering, and output delivery.
- command scope and error handling.
- version output and packaged skill loading.
- reusable configuration and path infrastructure when required.

Project loading MUST be limited to commands requiring project configuration.
Skill fixtures MUST live in `vcsinstall/skills/fixtures/` under the library's resource-loading convention.

## Submodules

Submodules are optional unless required elsewhere.
Conventional names MUST stay consistent:

- `utils` — Non-domain utilities.
- `errors` — Environment models/internal exceptions.
- `entities` — Owned types/entities.
- `fixtures` — Reusable configuration/docs/data.
- `tests` — Module tests.
- `tests.make` — Test-object construction.
- `tests.helpers` — Setup, assertions, and coordinated actions.

Tests SHOULD live under the nearest owning package's `tests`, named with the parent's `test_` prefix.
Production MUST NOT import test utilities.

## Cross-module dependencies

Top-level modules MUST expose explicit stable boundaries through package roots or declared public submodules.
Cross-module imports MUST use declared boundaries, never undeclared internals; same-owner submodules MAY import directly.
Initializers SHOULD export ergonomic common names and define `__all__`, but MUST NOT be required to re-export every submodule name.
Roots without useful APIs MAY be empty.

Configured dependency checks MUST enforce the specified ownership and dependency direction.
The CLI MAY depend on skills; skills MUST NOT depend on the CLI.
Tests MAY depend on the production modules they exercise, but production MUST NOT depend on tests.
Circular dependencies MUST be prohibited.
Check configuration MUST NOT permit undeclared dependencies merely to silence a finding.
