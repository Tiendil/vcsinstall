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
- `workspace` — Project configuration and lockfile state, with its loading, consistency checks, and persistence.
- `filesystem` — The filesystem adapter, local content inspection, and filesystem effects.
- `vcs_clients` — Invocation of native VCS clients and interpretation of their command results.
- `sources` — The source adapter and backend implementations for repository semantics, revision resolution, and content extraction from available repository views.
- `hosting` — The hosting adapter and implementations for repository acquisition, refresh, change delivery, and hosted reviews.
- `cache` — Disposable repository storage and coordinated cache access.
- `runtime` — Command-independent operation policies and coordination.
- `cli` — Command parsing, application setup, and shared command registration.
- `skills` — Document definitions and packaged Markdown.

The executable and `python -m vcsinstall` MUST use the same CLI entry point.
The package root MUST NOT duplicate distribution version metadata.
Implementations MUST grow with required behavior under these responsibilities; the intended layout MUST NOT require empty placeholder packages.

## Project state and operations

Universal file-identity values and their collection invariants MUST belong to `domain` and be reused across project and persisted content models.

`workspace` MUST own configuration and lockfile state under `specs/behavior/config.md` and `specs/behavior/lockfile.md`, including loading, matching, and persistence.
It MUST own project operation coordination, including per-user state-directory selection, project identity, acquisition, and lock-access diagnostics.
Coordination MUST use `platformdirs` for the application state location and native `filelock` locking with persistent coordination files.
The CLI MUST retain the workspace-owned coordination scope through command completion, acquiring it before project loading or initialization and before repository-cache access.
Workspace loading without coordination MAY remain available to callers that supply their own operation scope.
Initialization MUST reuse `llm_tool_cli` configuration creation, validation, project activation, and output facilities within the project coordination scope.
Entry destination overlap and project-file exclusion rules MUST belong to `workspace.validation` and MAY be exposed as pure guards through the workspace package root.
Configuration and lockfile models MUST be the shared representations of declarations and recorded state.
They MUST represent declarations with missing or mismatching locks and baselines where the locking contract permits that state.
Reusable checks of relationships between those models MUST belong to `workspace` and be exposed through specific validation methods on the loaded workspace.
Their implementations SHOULD be grouped in `workspace.validation` and remain implementation details for callers outside `workspace`.
These checks MUST operate on the loaded models, using the project root where required, without requiring a separate project-state representation solely for validation.
They MUST compare validated canonical entry destinations directly without normalizing them again.
They MUST NOT acquire repositories, inspect installed file contents, mutate project files, or emit output.
Matching MUST use source-owned repository and effective-selector rules without branching on backend names; source-owned rules MUST determine whether an omitted selector can reuse a recorded selection.
Rendering configuration and lockfile bytes MUST be a pure transformation of validated data, without filesystem access or path revalidation.
Loading and authorized writes MUST own the applicable filesystem checks.
It MUST own persisted content record representations and directory-specific manifest constraints while reusing shared file-identity values.
It MUST preserve the distinction between declarations and recorded installation baselines without duplicating operation policies.
Loading MUST preserve those distinctions without enforcing operation-specific preconditions.
Workspace loading MUST apply source-owned, effect-free validation of backend-specific definitions and lock fields before returning the loaded workspace.
Backend validation and retained-default interpretation MUST NOT require repository acquisition, cache access, or native-client execution.

`runtime` MUST coordinate each complete requested use case, including read-only operations and selections spanning multiple dependencies or entries.
It MUST own pure rules protecting local content and authorizing operation state changes under `specs/behavior/locking.md`, grouped in `runtime.validation` where shared by operations.
These guards MUST evaluate supplied facts without filesystem access, native-client execution, persistence, backend-name branches, or backend-specific selector rules.
It MUST choose and invoke workspace validation methods for the guarantees required by that operation and its selection, before dependent source acquisition or project mutation.
Missing, mismatching, or obsolete records MUST be rejected or reconciled according to the operation contract rather than through a universal consistency check during workspace loading.
It MUST consume dependency declarations with an already selected backend and normalized repository locator, independently of invocation directories and CLI backend options.
It MUST supply observed facts to its validation guards and coordinate external effects and persistence through their owning boundaries under the operation's behavior contract.
It MUST determine the revision and freshness requirements of the requested operation, coordinate acquisition or refresh when needed, and use source capabilities to resolve versions and extract content from the resulting repository view.
It MUST delegate backend-specific input validation and selector interpretation to source adapters rather than branch on backend identifiers or selector kinds.
Project mutation and contribution effects MUST retain their separate consistency and failure guarantees.
The operation layer MUST own the sequence of inspection, whole-selection validation, filesystem effects, and persistence.
It MUST use its validation guards and workspace rules to decide permitted changes and determine file copies and removals from inspected data before or during execution after validation succeeds.
Partial-operation diagnostics and installation-state reporting MUST belong to this layer rather than filesystem primitives.
Only capabilities needed by the requested operation MUST be invoked; hosting and cache access MUST remain optional where their behavior contracts permit.
Runtime operations MAY be grouped into implementation submodules by coherent use cases without creating a top-level package for each CLI command.

`cli` MUST pass prepared input data to runtime rather than implement project policy or construct operation result payloads.
It MUST own validation of selector-option combinations and conflicts with a repository URL's version selection, and apply accepted option values to the parsed repository request before invoking runtime.
It MUST coordinate ordered repository detection through source and hosting facilities when no backend option is supplied, normalize the selected locator against the invocation directory, and pass a prepared dependency declaration to runtime.
Command results MUST follow `specs/architecture/entities.md`; shared utility commands MUST retain their `llm_tool_cli` contracts.

## Adapter and cache boundaries

The filesystem adapter MUST own local filesystem access without taking ownership of dependency selection or installation-baseline policy.
The ordinary filesystem source backend MUST belong to `sources`, using filesystem facilities under its source contract; local filesystem access and source-version semantics MUST remain distinct responsibilities.
Reusable filesystem inspection and execution primitives MUST be exposed through a root-bound `FSClient` in `filesystem`, using shared path normalization and containment facilities.
Filesystem primitives MUST NOT own baseline comparisons, file-ownership policy, operation change plans, or the sequencing of validation and effects across a command's selection.
Each client MUST accept a normalized root and default to read-only access through its `readonly` flag.
Read-only clients MAY inspect content, but MUST reject mutation before any filesystem effect.
Clients inspecting external source repositories MUST use read-only access; authorized destination writes MUST explicitly use writable access.
Streaming file-copy APIs MUST require the source to have been inspected as a regular file for the operation, without repeating source-kind inspection while copying.
Callers MUST account for changes initiated by the current invocation before reusing that inspection.
Operation paths MUST be interpreted relative to the explicitly selected client root, and filesystem effects MUST enforce that root's containment and read-only constraints.
Shared filesystem data models and pure hashing functions MUST remain separate from client I/O.
Source and local filesystem inspections MUST share one file-focused content-tree representation containing the inspected root kind and regular-file metadata.
Inspection scope MUST be determined by the requested operation; structural directory positions MUST NOT require a separate content model or directory collection.
Reusable content-identity computations MUST remain pure and independent of project loading and external clients so local inspection and source preparation can use the same rules.
File selection, entry destination ownership, and identity semantics MUST follow `specs/behavior/files-and-directories.md` at the respective operation, persistence, and adapter boundaries.
Runtime operations MUST validate the complete selected entry trees before effects and coordinate whole-entry replacement; filesystem primitives MUST only execute the authorized removal and streaming writes.

Source adapters MUST own repository and version semantics, revision resolution, and content extraction from available repository views under `specs/behavior/vcs-interaction.md`.
They MUST provide effect-free input validation separately from persisted declaration and lock validation.
Input validation MUST accept supported forms requiring resolution, including abbreviated revisions and unresolved selectors where the backend permits them; persisted validation MUST enforce the resolved representation.
Operations MUST complete source input validation before repository acquisition and pass validated input to resolution.
They MUST own backend-specific locator recognition and normalization and read-only local repository probes.
Adapter probes MUST recognize their own backend prefixes and native notation; common dispatch MUST NOT perform a separate syntax-based backend-selection pass.
Normalization MUST return a successful optional locator, with no value for format rejection; probes MUST return a successful false result for rejected values and other non-matches.
Both boundaries MUST retain operational errors through shared results, and format-exception handling MUST remain local to the parsing operation that can produce it under `specs/architecture/errors.md`.
Repository URL parsing MUST accept the locator alone and return plain typed repository and version intent without receiving CLI option values or selecting a backend.
It MUST retain shared source-version syntax without taking ownership of native backend locator semantics.
An explicit backend option MUST select its adapter without automatic probes or a separate locator/backend consistency check.
Automatic detection MUST have an explicit order with specialized backends before filesystem.
Parsed source requests MUST retain the repository locator and one optional version selector independently of backend selection, using the shared selector representation under `specs/architecture/entities.md` for typed and unresolved input alike.
The CLI input boundary MUST construct the dependency declaration from the completed source request after backend selection and locator normalization.
Resolution MUST accept that declaration and the acquired local repository path, resolve any unresolved selector kind within the declaration, and return plain source data binding the effective dependency declaration, repository path, and selected immutable revision.
Backend-specific reference metadata MUST remain in the backend's native repository representation.
Interpretation of an omitted selector MUST belong to the applicable source adapter rather than shared source-data fields.
Source adapters MUST separate metadata inspection from streaming content copying and receive the bound resolved source data for both.
Operations using an existing lock MAY bind the recorded dependency and revision to an acquired path directly, without resolving the current selector again.
Production source adapters MUST NOT retain invocation-specific state between calls.
Inspection results and operation data entities retained between phases MUST contain plain typed data rather than clients, adapters, callbacks, or native file references.
Native file-reference handling MUST remain within the backend and its native client.
Shared file metadata MUST NOT encode backend-specific references as a common string identifier.
Adapters and runtime MUST follow the invocation state stability assumption in General behavior in `specs/behavior/cli.md` rather than add concurrent-change guards or copy-time rehashing solely to detect external changes.
Their allowed effects MUST follow Backend selection and capabilities and Native client execution in that specification; filesystem source semantics MUST follow `specs/behavior/vcs/filesystem.md`.
The default source-adapter registry MUST cover every recognized source backend.
Replacement registries supplied at input or loading boundaries MUST preserve that complete coverage so selecting a recognized backend always supplies an adapter.
Runtime operations MAY accept independently supplied source and hosting adapters.
Production callers supplying adapters MUST select the source adapter using the prepared dependency's backend and obtain default hosting through the hosting module.
Runtime operations adding entries or spanning multiple dependencies MUST use the shared source and hosting selection facilities directly rather than require caller-supplied adapters or selection callbacks.
Backend and hosting selection policies MUST remain in their owning modules rather than be duplicated in runtime operations.
Hosting selection MUST NOT acquire repositories or perform cache filesystem access.

The cache module MUST own an invocation-scoped binding for the normalized cache root, using `llm_tool_cli`'s shared context facilities.
The CLI MUST resolve the root and activate its scope before invoking operations that may use the cache, restoring any previous binding when the invocation ends, including failure paths.
This binding MUST remain independent of workspace activation so project-independent cache operations can use it.
Cache-backed hosting MUST obtain its normalized root from this binding when constructed; each repository cache instance MUST retain its explicitly bound root.

Hosting adapters MUST own repository acquisition, refresh, and complete outgoing workflows under `specs/behavior/vcs-interaction.md`, including:

- native repository acquisition and transport authentication.
- remote repository metadata probes used for backend recognition.
- discovery and refresh of reference metadata, including advertised default branches.
- making required repository data available for source inspection and extraction.
- preparation of selected source-path changes and native contribution commits.
- destination selection and publication to branches or filesystem sources.
- patch generation and export.
- hosted repository identification, permissions, and fork management where required.
- creation or lookup of review requests where required.

Acquisition MUST expose a context manager yielding a shared result containing the requested repository's normalized local path; repository identity MUST remain supplied by the requesting operation rather than be echoed in a separate acquisition-result entity.
Cache-backed hosting MUST own repository-cache construction and locking through that acquisition scope, retaining exclusive access until all source consumers in the scope finish, including failure paths.
Operations MUST keep resolution, inspection, and copying inside the hosting acquisition scope without implementing backend-specific cache or hosting branches.
Git hosting MUST own native Git clone/fetch, remote-reference discovery, commit preparation, branch and direct delivery, and patch generation.
GitHub hosting MUST compose Git hosting with GitHub-specific repository and review capabilities.
Filesystem hosting MUST provide access to the declared source directory and apply authorized source writes through the lower-level filesystem adapter; it MUST remain distinct from that adapter and the filesystem source adapter.
Noop hosting MUST simulate acquisition, refresh, and outgoing workflows independently of the selected source adapter.
These implementations MUST share the hosting-adapter boundary rather than introduce another publication-adapter layer.
Hosting selection MUST belong to one hosting-owned boundary shared by acquisition and outgoing workflows, with delivery compatibility checked by the selected adapter.

Reusable local repository-inspection capabilities MAY be exposed by `sources` for hosting implementations.
`vcs_clients` MUST own native VCS command execution and reusable object and reference inspection, including parsing native command output.
Clients MAY retain local repository paths and repository locators for repeated operations.
Native inspection MUST retain object identities, file modes, and path bytes so adapters can apply their behavior contracts to selected content.
Source adapters MUST retain selector inference and content-selection policy, and hosting adapters MUST retain acquisition, refresh, and publication decisions.
Shared client operations and cached objects MUST preserve these ownership boundaries.

Adapter boundary data and failures MUST follow `specs/architecture/entities.md` and `specs/architecture/errors.md`.
Source and hosting adapters MUST NOT load or persist the target project's declarations or baselines, assemble CLI result payloads, or perform terminal output.
External I/O MUST remain with its owning adapter, VCS client, cache, or persistence boundary.

`cache` MUST own reusable storage and access coordination under `specs/behavior/cache.md`.
Source and hosting adapters MAY use it according to their behavior contracts; cache management MUST NOT take ownership of requested versions, installation baselines, or durable contribution history.
Source and publication repository identities MUST remain distinct when prepared source objects are published elsewhere.

## External integrations

Real VCS operations MUST use installed native clients rather than implement their wire protocols.
Git source access and Git hosting MUST use the installed `git` client through `vcs_clients`.
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
- Within vcsinstall, `filesystem` MAY depend only on `core` and `domain`.
- `workspace` MAY depend on `filesystem` for local persistence.
- `cache` MAY depend on `filesystem` for its local storage.
- Within vcsinstall, `vcs_clients` MAY depend only on `core` and `domain`.
- `sources` MAY depend on `filesystem`, `cache`, and `vcs_clients`; it MUST NOT depend on `hosting`.
- `hosting` MAY use public local repository-inspection capabilities from `sources`, and public filesystem, cache, and VCS client capabilities.
- Workspace, adapter, and cache modules MUST NOT depend on `runtime` or `cli`, and adapters and cache MUST remain independent of `workspace`.
- `runtime` MAY compose workspace, adapters, and cache through their public boundaries while keeping shared operation guards within runtime.
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
