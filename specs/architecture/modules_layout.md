# Module structure

## Goal of the document

This document describes module responsibilities, public boundaries, dependency directions, and shared integration choices.

## Scope

This specification covers project module organization and architectural responsibilities.
The following are out of scope:

- detailed implementation.
- observable operation behavior and persisted formats.
- entity/error/test conventions beyond placement.

## Dictionary

- `module` — a Python package/module owning coherent functionality.
- `submodule` — a narrower responsibility within a module.
- `test submodule` — tests of a parent module/submodule.

## Modules

All vcsinstall code MUST belong under `vcsinstall/`.

- `core` — Project-wide error-base extensions and domain-independent technical utilities.
- `domain` — Universal concepts, semantic values, and pure logic shared by all or most modules, independently of input interfaces, output protocols, and persisted syntax.
- `project` — Managed dependency and entry state, including revision locks and installation baselines, and the invariants governing that state.
- `workspace` — Project configuration and lockfile representations, with their loading and persistence.
- `filesystem` — The filesystem adapter, local content inspection, and filesystem effects.
- `sources` — The source adapter and backend implementations for repository semantics, revision resolution, and content extraction from available repository views.
- `hosting` — The hosting adapter and implementations for repository acquisition, refresh, change delivery, and hosted reviews.
- `cache` — Disposable repository storage and coordinated cache access.
- `runtime` — Command-independent operation coordination.
- `cli` — Command parsing, application setup, and shared command registration.
- `skills` — Document definitions and packaged Markdown.

The executable and `python -m vcsinstall` MUST use the same CLI entry point.
The package root MUST NOT duplicate distribution version metadata.
Implementations MUST grow with required behavior under these responsibilities; the intended layout MUST NOT require empty placeholder packages.

## Project state and operations

`project` MUST own the logical project state and decisions preserving its invariants under `specs/behavior/locking.md`.
Its model MUST remain independent of configuration and lockfile syntax.
It MUST evaluate supplied facts without performing filesystem access, native-client execution, or persistence.
It MUST represent declarations with missing or mismatching locks and baselines where the locking contract permits that state.
Project models and matching MUST NOT branch on backend names or implement backend-specific selector requirements.
They MUST compare effective-selector facts supplied by their caller; source-owned rules MUST determine whether an omitted selector can reuse a recorded selection.

Universal file-identity values and their collection invariants MUST belong to `domain` and be reused across project and persisted content models.

`workspace` MUST load and persist that state under `specs/behavior/config.md` and `specs/behavior/lockfile.md`.
It MUST own persisted content record representations and directory-specific manifest constraints while reusing shared file-identity values.
It MUST preserve the distinction between declarations and recorded installation baselines without duplicating project-owned policies.
Loading MUST preserve those distinctions without enforcing operation-specific preconditions.
Workspace loading MUST apply source-owned, effect-free validation of backend-specific definitions and lock fields before constructing project state.
Backend validation and retained-default interpretation MUST NOT require repository acquisition, cache access, or native-client execution.

`runtime` MUST coordinate each complete requested use case, including read-only operations and selections spanning multiple dependencies or entries.
It MUST supply observed facts to the project model and coordinate external effects and persistence through their owning boundaries under the operation's behavior contract.
It MUST determine the revision and freshness requirements of the requested operation, coordinate acquisition or refresh when needed, and use source capabilities to resolve versions and extract content from the resulting repository view.
Project mutation and contribution effects MUST retain their separate consistency and failure guarantees.
Only capabilities needed by the requested operation MUST be invoked; hosting and cache access MUST remain optional where their behavior contracts permit.
Runtime operations MAY be grouped into implementation submodules by coherent use cases without creating a top-level package for each CLI command.

`cli` MUST pass parsed intent to runtime rather than perform project policy or reconstruct operation payloads.
Command results MUST follow `specs/architecture/entities.md`; shared utility commands MUST retain their `llm_tool_cli` contracts.

## Adapter and cache boundaries

The filesystem adapter MUST own local filesystem access without taking ownership of dependency selection or installation-baseline policy.
The ordinary filesystem source backend MUST belong to `sources`, using filesystem facilities under its source contract; local filesystem access and source-version semantics MUST remain distinct responsibilities.
Reusable content-identity computations MUST remain pure and independent of project loading and external clients so local inspection and source preparation can use the same rules.
File selection, manifest ownership, and identity semantics MUST follow `specs/behavior/files-and-directories.md` at the respective project, persistence, and adapter boundaries.

Source adapters MUST own repository and version semantics, revision resolution, and content extraction from available repository views under `specs/behavior/vcs-interaction.md`.
Their allowed effects MUST follow Backend selection and capabilities and Native client execution in that specification; filesystem source semantics MUST follow `specs/behavior/vcs/filesystem.md`.

Hosting adapters MUST own repository acquisition, refresh, and complete outgoing workflows under `specs/behavior/vcs-interaction.md`, including:

- native repository acquisition and transport authentication.
- discovery and refresh of reference metadata, including advertised default branches.
- making required repository data available for source inspection and extraction.
- preparation of selected source-path changes and native contribution commits.
- destination selection and publication to branches or filesystem sources.
- patch generation and export.
- hosted repository identification, permissions, and fork management where required.
- creation or lookup of review requests where required.

Git hosting MUST own native Git clone/fetch, remote-reference discovery, commit preparation, branch and direct delivery, and patch generation.
GitHub hosting MUST compose Git hosting with GitHub-specific repository and review capabilities.
Filesystem hosting MUST provide access to the declared source directory and apply authorized source writes through the lower-level filesystem adapter; it MUST remain distinct from that adapter and the filesystem source adapter.
Noop hosting MUST simulate acquisition, refresh, and outgoing workflows independently of the selected source adapter.
These implementations MUST share the hosting-adapter boundary rather than introduce another publication-adapter layer.

Reusable local repository-inspection capabilities MAY be exposed by `sources` for hosting implementations.
Shared native-client primitives and cached objects MUST preserve source ownership of repository semantics and hosting ownership of acquisition, authentication, and delivery.

Adapter boundary data and failures MUST follow `specs/architecture/entities.md` and `specs/architecture/errors.md`.
Source and hosting adapters MUST NOT load or persist the target project's declarations or baselines, assemble CLI result payloads, or perform terminal output.
External I/O MUST remain with its owning adapter, cache, or persistence boundary.

`cache` MUST own reusable storage and access coordination under `specs/behavior/cache.md`.
Source and hosting adapters MAY use it according to their behavior contracts; cache management MUST NOT take ownership of requested versions, installation baselines, or durable contribution history.
Source and publication repository identities MUST remain distinct when prepared source objects are published elsewhere.

## External integrations

Real VCS operations MUST use installed native clients rather than implement their wire protocols.
Git source access and Git hosting MUST use the installed `git` client through reusable native-client integration.
GitHub-specific hosting operations MUST use the installed `gh` client, including its authenticated API facilities where convenience commands do not cover the required hosted operation.
These operations MUST use the client's configured account and host rather than derive an API identity from Git commit authorship.

Service-specific tools MUST be required only by operations needing that service's capabilities.
Git acquisition, local source inspection, and native delivery MUST remain independent of `gh` availability and login state, including when GitHub hosting delegates acquisition or native delivery to Git hosting.
Filesystem and noop source and hosting operations MUST NOT require native VCS clients.
Credential configuration and commit identity MUST remain owned by the native clients; vcsinstall MUST NOT introduce another credential store in project declarations or lock state.
Native-client failures and effects MUST follow `specs/behavior/vcs-interaction.md` and its source and hosting contracts.

## Shared infrastructure

`core` MUST own vcsinstall's project-wide technical foundations while reusing the infrastructure supplied by `llm_tool_cli`.
Project error-base extensions MUST follow `specs/architecture/errors.md`.

vcsinstall MUST use application construction and shared skill/version command registration from `llm_tool_cli`, supplying its document definitions and distribution identity `vcsinstall`.
Static application settings MUST be initialized before command-line parsing, using the tool label `VCSINSTALL`.
Static settings MUST supply the default project configuration filename from `specs/behavior/config.md` before parsing.

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

Test placement, naming, and utility responsibilities MUST follow Test module layout in `specs/architecture/tests.md`.

## Cross-module dependencies

- All modules MAY use public technical foundations from `core`; `core` MUST NOT depend on other vcsinstall modules.
- All modules except `core` MAY use universal values and pure logic from `domain`; `domain` MAY depend only on `core` within vcsinstall.
- Within vcsinstall, `project` and `filesystem` MAY depend only on `core` and `domain`.
- `workspace` MAY depend on `project` and `filesystem` for model conversion and local persistence.
- `cache` MAY depend on `filesystem` for its local storage.
- `sources` MAY depend on `filesystem` and `cache`; it MUST NOT depend on `hosting`.
- `hosting` MAY use public local repository-inspection capabilities from `sources`, and public filesystem and cache capabilities.
- Workspace, adapter, and cache modules MUST NOT depend on `runtime` or `cli`, and adapters and cache MUST remain independent of `project` and `workspace`.
- `runtime` MAY compose the model, workspace, adapters, and cache through their public boundaries.
- `cli` MAY use those public boundaries for application setup and `skills` for documentation while leaving operation coordination to runtime.
- `skills` MUST remain independent of other vcsinstall modules except `core` and `domain`.

Top-level modules MUST expose explicit stable boundaries through package roots or declared public submodules.
Cross-module imports MUST use declared boundaries, never undeclared internals; same-owner submodules MAY import directly.
Initializers SHOULD export ergonomic common names and define `__all__`, but MUST NOT be required to re-export every submodule name.
Roots without useful APIs MAY be empty.

Configured dependency checks MUST enforce the specified ownership and dependency direction.
Tests MAY depend on the production modules they exercise, but production MUST NOT depend on tests.
Circular dependencies MUST be prohibited.
Check configuration MUST NOT permit undeclared dependencies merely to silence a finding.
