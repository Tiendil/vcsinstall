# vcsinstall implementation plan

## Goal of the document

This document organizes implementation of the completed vcsinstall specifications into small stages that can be implemented and reviewed independently.

## Scope

The plan covers the path from the existing CLI scaffold to the specified Git and filesystem installation and contribution workflows, including tests, packaged documentation, and integration with `llm_tool_cli`.
Additional source backends, HTTP support, publication history, automatic merging, and release publication are outside this plan.
The owning behavior and architecture specifications remain the source of requirements; this plan describes implementation order rather than a competing product contract.

## Starting point and ordering

The repository already has Docker development helpers, package and release scaffolding, Donna and Depmesh configuration, and shared help, version, and skill commands.
Those foundations should be extended rather than recreated.
Donna and Depmesh provide local examples of shared application construction, project scopes, command contexts, and test organization; vcsinstall's specifications determine its own module boundaries.

Stages are ordered by their prerequisites and normally run in the listed order.
A stage may finish at a public Python boundary without adding a CLI command; such a stage must provide useful, tested behavior through that boundary.
Only completed behavior is registered or advertised, as permitted by [CLI entry points](../behavior/cli.md#entry-points).
Production commands must reject test-only adapters, and incomplete adapter capabilities must not be advertised.

## Completion and review gate for every stage

Each stage's final checkbox includes all of the following:

- Keep all previously implemented behavior working, with no skipped failures, placeholder commands, or unfinished production paths in the implemented scope.
- Add tests alongside the changed behavior under [the test architecture](../architecture/tests.md). Prefer real temporary files and local repositories; use independently supplied source and hosting collaborators for orchestration tests. Do not add tests merely for passive fields or inherited shared-library behavior.
- Check the affected specifications, code, and tests together. Preserve shared diagnostics and use shared results, command contexts, logic cells, and output delivery directly. Runtime constructs complete command payloads; the CLI passes parsed intent.
- Maintain package exports and the module restrictions in [module structure](../architecture/modules_layout.md). Extend `tach.toml` and applicable `depmesh.toml` mappings as real responsibilities appear, without creating empty packages or weakening dependency checks.
- Update help, `README.md`, and packaged usage documentation in the same stage when available behavior changes. Remove the scaffold's obsolete claim that the configuration and lockfile formats are undecided when first aligning those documents with the completed specs. Record notable changes through the existing Changy sources and generation workflow.
- Run `donna -p llm run @/workflows/polish.donna.md` for implementation stages, with development operations executed through repository container helpers. Run `./bin/dev-check-runtime.sh` when packaged resources, runtime dependencies, entry points, or installed-package behavior change. Research and documentation stages may omit runtime tests when no runtime behavior changes, but must leave the existing implementation consistent.
- Record review evidence: implemented scope, checks and results, remaining limitations, and any concrete follow-up stages. Mark a stage complete only after its own work and this gate are complete.

Before adding rich domain entities, obtain the explicit approval required by [Domain entities](../architecture/entities.md#domain-entities); data entities with pure policy functions remain an available design.
Dependency, lockfile, Docker, and tool-installation changes require the authorization described in `AGENTS.md`; a research finding must become a concrete prerequisite task before dependent implementation proceeds.
Product specification changes require a separate explicit request and an index update; discovering an ambiguity does not authorize rewriting its contract.

## Maintaining the plan

Research stages produce concrete decisions, experiments, and revised checklists rather than speculative production abstractions.
Task-local supporting artifacts may live under `.session/` and be linked from the relevant stage, but decisions needed to execute the plan must also be summarized here because session storage is temporary.
Split a stage before implementation if its concrete design contains several independently reviewable features.
Update later stages when a completed slice establishes a clearer interface or reveals a prerequisite, preserving a working review boundary after every replacement stage.

## Stage 1 — Research shared infrastructure and implementation prerequisites

Relevant contracts: [module structure](../architecture/modules_layout.md), [entities](../architecture/entities.md), [errors](../architecture/errors.md), [cache](../behavior/cache.md), and the adopted `llm-tool-cli` contracts.

- [ ] Compare the pinned `llm-tool-cli==0.2.0` API with the sibling checkout and the Donna/Depmesh integrations; identify the public APIs for project loading, initialization, paths, results, command scopes, output, and test fixtures.
- [ ] Resolve how to add invocation-local `--cache-dir` while retaining shared application construction and shared ownership of `--protocol` and `--config`. The inspected shared constructor currently accepts only application help and declares the two shared options; identify an extension or a separately authorized shared-library prerequisite instead of copying its callback infrastructure.
- [ ] Choose a configuration/lockfile TOML-writing approach and cache-lock mechanism, including comment-preservation tradeoffs, unchanged-byte guarantees, concurrent metadata changes, and ordering of file and metadata effects. Identify any required direct dependencies or Docker/native-client changes without applying them in this research stage.
- [ ] Record the proposed project-model style, approval needs for any rich domain entities, and native Git/`gh` capabilities needed for the specified object formats and hosted workflows. Keep network-free test strategies explicit.
- [ ] Update this plan with concrete prerequisite stages and decisions; complete the common review gate with the existing scaffold still working.

## Stage 2 — Technical foundations and semantic values

Relevant contracts: [entities](../architecture/entities.md), [errors](../architecture/errors.md), [naming](../architecture/naming.md), [configuration](../behavior/config.md), and [source paths and identity](../behavior/vcs-interaction.md).

- [ ] Add the project-wide environment-error and internal-error bases in `core`, directly extending the shared foundations without another result, entity-base, or rendering abstraction.
- [ ] Introduce the semantic values and closed enums needed by the next slices, including dependency names, repository identity, selectors, source paths, and revisions; retain backend-specific revision validation at its owner.
- [ ] Implement local validation and normalization without I/O, preserving literal selector and path text where the contracts require it, including whitespace, case, and `+` characters.
- [ ] Test meaningful validation and conversion paths, including root source paths, traversal rejection, invalid dependency names, and selector exclusivity; complete the common review gate.

## Stage 3 — File manifests and content identity

Relevant contracts: [files and directories](../behavior/files-and-directories.md) and [hash representation](../behavior/lockfile.md#hash-representation).

- [ ] Implement reusable pure representations of file, directory, and absent content, with directory manifests identifying regular files rather than independently versioned children.
- [ ] Validate literal UTF-8 manifest paths, uniqueness, file-ancestor conflicts, and the distinction between the root-file comparison position `.` and directory child paths.
- [ ] Implement exact-byte file hashes and the specified UTF-8-byte-sorted directory hash encoding, including empty manifests and aggregate-hash verification.
- [ ] Test binary bytes, enumeration-order independence, Unicode names, invalid manifests, empty-file/directory distinctions, and the published hash examples; complete the common review gate.

## Stage 4 — Project state and declaration matching

Relevant contracts: [project ownership](../architecture/modules_layout.md#project-state-and-operations) and [locking](../behavior/locking.md#matching-configuration).

- [ ] Represent declarations, dependency locks, and entry baselines independently of TOML, filesystem access, or adapter execution, including declared-but-unlocked states and dependencies without entries.
- [ ] Implement repository/selector matching and entry-binding matching, including selector-less Git declarations adopting a matching recorded branch and explicit branch declarations matching that same inferred branch.
- [ ] Retain old valid bindings separately from current declaration matches so later replacement checks can protect previous installations and unselected entries.
- [ ] Test independent dependencies sharing a repository, missing/mismatching records, exact declared-set matching, inconsistent baselines, and declaration changes that do not change identity; complete the common review gate.

## Stage 5 — Project configuration loading

Relevant contracts: [configuration](../behavior/config.md), [workspace ownership](../architecture/modules_layout.md#project-state-and-operations), and the shared configuration/path contracts.

- [ ] Add the version-1 workspace configuration schema and conversion to project declarations, reusing shared discovery, version validation, loading, and project scopes.
- [ ] Normalize configured destinations relative to the selected project root and filesystem repository locators using their own base, without requiring source or installed content to exist.
- [ ] Validate complete configuration, dependency references, unique/nonoverlapping destinations, and conflicts with the selected configuration and sibling lockfile; preserve shared diagnostic context.
- [ ] Test discovery and explicit paths, nested invocation directories, minimal/empty/invalid TOML, unknown fields, strict versions, literal strings, containment, and absence of source/cache/client access; complete the common review gate.

## Stage 6 — Starter configuration and `init`

Relevant contracts: [starter configuration](../behavior/config.md#starter-configuration), [init](../behavior/cli.md#vcsinstall-init-command), and [skill fixtures](../behavior/skill_fixtures.md).

- [ ] Package the specified starter template with active version 1 and commented dependency/entry examples.
- [ ] Register initialization through `llm_tool_cli`, retaining its target selection, exclusive creation, validation, result, and error contracts.
- [ ] Test local and explicit initialization targets, existing targets and symlinks, parent-project discovery independence, all protocols, and the absence of lockfile, cache, and source effects.
- [ ] Update available-command documentation and verify the template from an installed wheel; complete the common review gate.

## Stage 7 — Lockfile parsing and validation

Relevant contracts: [lockfile](../behavior/lockfile.md) and [declaration matching](../behavior/locking.md#matching-configuration).

- [ ] Load only the exact sibling `vcsinstall.lock`, distinguishing absence from unreadable, malformed, symlink, and non-regular-file targets.
- [ ] Implement independent version-1 lockfile validation and conversion to project state, including complete revisions, selector consistency, baseline-kind fields, manifests, and aggregate hashes.
- [ ] Validate recorded destination conflicts and manifest structure independently of active declarations; preserve old valid records and defer operation-specific preconditions to their owners.
- [ ] Test minimal and missing files, malformed records, unsupported versions, conflicting paths, absent baselines, stale declarations, and loading without installed/source/cache access; complete the common review gate.

## Stage 8 — Baseline-scoped local filesystem inspection

Relevant contracts: [local comparison](../behavior/files-and-directories.md#local-comparison), [directories](../behavior/files-and-directories.md#directories), and shared containment.

- [ ] Add filesystem inspection of explicitly supplied managed paths, entry roots, and required parents, returning typed observations without interpreting project policy.
- [ ] Distinguish exact file content, genuine absence, structural directories, root/parent type obstructions, and read failures.
- [ ] Read only the recorded comparison scope; do not hash unmanaged descendants or consult target-VCS tracking, staging, or ignore rules.
- [ ] Test partially missing trees, empty-directory equivalence, unexpected files/directories, symlinks, binary content, and unreadable or unsupported objects inside versus outside scope; complete the common review gate.

## Stage 9 — Local installation status

Relevant contracts: [local state inspection](../behavior/locking.md#local-state-inspection) and [local comparison](../behavior/files-and-directories.md#local-comparison).

- [ ] Compute `clean`, `modified`, `missing`, and `unlocked` in `project` from declaration matches and supplied filesystem observations.
- [ ] Apply modified-over-missing precedence, expected absence, empty-manifest equivalence, and the rule that unmanaged additions alone do not change ordinary status.
- [ ] Keep malformed/unreadable state as an error and keep old bindings from becoming the apparent baseline of a new mapping.
- [ ] Test mixed missing/modified paths, type changes, absent baselines, stale/missing locks, and repeated observations; complete the common review gate.

## Stage 10 — Entry inspection through `list`

Relevant contracts: [entry selection](../behavior/cli.md#local-path-arguments-and-entry-selection), [list](../behavior/cli.md#vcsinstall-list-command), and [installation records](../behavior/cli.md#installation-records).

- [ ] Implement canonical entry selection with shared mixed-path normalization, invocation-directory bases, exact root matching, deduplication, unknown-path rejection, and missing-target support.
- [ ] Add runtime coordination of project loading, lockfile loading, inspection, and complete installation logic cells; keep CLI handling limited to parsed intent and shared command scope.
- [ ] Register `list` with canonical-destination ordering, explicit dependency labels, and metadata-only automation records in all protocols.
- [ ] Test empty/default/explicit selections, unlocked and changed entries, `--` positional handling, retained shared diagnostics, repeated invocation isolation, and no cache/client/source access; complete the common review gate.

## Stage 11 — Dependency inspection through `dependency list`

Relevant contracts: [dependency list](../behavior/cli.md#vcsinstall-dependency-list-command) and [dependency records](../behavior/cli.md#dependency-records).

- [ ] Add dependency summary logic cells with normalized source identity, effective selector, matching revision, and entry-status counts derived from the existing inspection path.
- [ ] Register the dependency command group and `list`, including bare group help without project preparation.
- [ ] Include dependencies without entries, preserve independent dependency names, and avoid upstream-freshness claims or installation records.
- [ ] Test counts and ordering, empty projects, inferred/default selectors, stale locks, unsupported arguments, and project/cache/client independence of group help; complete the common review gate.

## Stage 12 — Configuration editing

Relevant contracts: [configuration editing](../behavior/config.md#configuration-editing) and [preparation and consistency](../behavior/locking.md#preparation-and-consistency).

- [ ] Implement workspace edits of supplied dependency and entry declarations without making project-policy decisions or performing source access.
- [ ] Preserve unrelated meanings and, using the researched approach, comments/order where practical; write normalized repository fields and canonical destinations for changed declarations.
- [ ] Preserve bytes for logical no-ops and protect existing configuration against serialization, replacement, and detected concurrent-edit failures.
- [ ] Test add/replace/remove edit batches, literal-string round trips, custom configuration filenames, unchanged bytes, and failure preservation; complete the common review gate.

## Stage 13 — Lockfile persistence

Relevant contracts: [lockfile writes and failures](../behavior/lockfile.md#writes-and-failures).

- [ ] Serialize validated project state to the prescribed version-1 lockfile with stable record ordering and no cache paths, timestamps, or incidental machine state.
- [ ] Implement atomically visible replacement with concurrent-change protection and preservation of an existing file on serialization or replacement failure.
- [ ] Keep lockfile bytes unchanged for logical no-ops, and support record-only operations that must leave an absent lockfile absent.
- [ ] Test read/write round trips, deterministic manifests, concurrent replacement, first-write failure, existing-file failure, and no-op preservation; complete the common review gate.

## Stage 14 — Applying authorized filesystem changes

Relevant contracts: [filesystem effects](../behavior/files-and-directories.md#filesystem-effects) and [failures](../behavior/files-and-directories.md#failures).

- [ ] Implement filesystem application of supplied prepared file changes, including parent creation, individual owned-file deletion, and pruning only physically empty contained directories.
- [ ] Enforce containment and preservation of unmanaged content during file/directory type changes; never infer directory ownership from current descendants.
- [ ] Keep installed bytes independent of prepared/cached source storage, and return useful completed-effect information if application fails after earlier writes.
- [ ] Test file and directory changes, missing-file restoration, unmanaged descendants, symlinks and obstructions, storage independence, and partial failures; complete the common review gate.

## Stage 15 — Installation and update protection decisions

Relevant contracts: [installation protection](../behavior/locking.md#installation-protection), [updating baselines](../behavior/locking.md#updating-baselines), and [local comparison](../behavior/files-and-directories.md#local-comparison).

- [ ] Implement pure project decisions comparing prior baselines, observed local content, and proposed source snapshots over the complete union of old and proposed paths.
- [ ] Accept existing content wholly matching the proposed state or wholly matching the prior baseline, with the specified missing-file restoration allowance; reject mixtures requiring an implicit merge.
- [ ] Produce authorized changes and proposed baselines for source additions, deletions, expected absence, empty directories, and safe type changes without treating installed content as a source baseline.
- [ ] Test adopted upstream-equivalent local edits, missing children mixed with conflicts, proposed-path unmanaged collisions, source-integrity failures, and all-or-nothing preparation decisions; complete the common review gate.

## Stage 16 — Dependency and entry redefinition policies

Relevant contracts: [operation contracts](../behavior/locking.md#operation-contracts), [matching configuration](../behavior/locking.md#matching-configuration), and CLI replacement rules.

- [ ] Implement pure decisions for explicit dependency replacement and entry replacement, including moving an entry between dependencies while preserving memberships and unaffected shared locks.
- [ ] Use prior valid bindings to protect old installations without making them match new definitions; refuse selections that would discard protection still required by unselected declared entries.
- [ ] Preserve the distinction between replacement permission, local-content protection, and permission to use an outdated lock; represent unchanged replacements explicitly.
- [ ] Test changed repositories/selectors/source paths, dependency reassignment, old-baseline protection, identical replacements, and unselected-entry hazards; complete the common review gate.

## Stage 17 — Source boundary and controlled noop source

Relevant contracts: [source interaction](../behavior/vcs-interaction.md), [noop source](../behavior/vcs/noop.md), and [adapter tests](../architecture/tests.md#adapter-integration-boundaries).

- [ ] Introduce the typed source boundary for interpreting available repository views, resolving supported selectors, and extracting immutable content, without acquisition or delivery responsibilities.
- [ ] Implement explicitly test-supplied noop source fixtures with opaque immutable revisions, literal repository identities, exact bytes, and controlled defaults, references, availability, and failures.
- [ ] Keep source fixtures isolated and independently selectable from hosting; production registration must reject `noop`.
- [ ] Test selector ambiguity, unavailable revisions versus absent paths, empty directories, stable revision meaning, ordering independence, and propagation through the normal source-error boundary; complete the common review gate.

## Stage 18 — Hosting acquisition boundary and noop acquisition

Relevant contracts: [hosting selection](../behavior/vcs-interaction.md#hosting-selection-and-capabilities), [acquisition](../behavior/vcs-interaction.md#repository-acquisition-and-refresh), and [noop hosting](../behavior/hosting/noop.md).

- [ ] Introduce the independent typed hosting boundary for acquisition/refresh requests and observed repository views, including required revisions and freshness/default-reference metadata.
- [ ] Implement noop acquisition/refresh with controlled views and request observations, independently of source resolution and without native transport, cache effects, or implicit source-fixture mutation.
- [ ] Add capability selection and production rejection of test-only hosting; defer outgoing capabilities until their implementation stages rather than returning unconditional success.
- [ ] Test wrong-identity views, moved/deleted refs, failed refresh without stale fallback, independent fixtures, and composition with noop and test-prepared real views; complete the common review gate.

## Stage 19 — Live filesystem source access

Relevant contracts: [filesystem source](../behavior/vcs/filesystem.md) and [filesystem acquisition](../behavior/hosting/filesystem.md#directory-acquisition).

- [ ] Implement filesystem hosting acquisition of an existing readable directory and source interpretation of that live view, without creating a source or using the repository cache.
- [ ] Fingerprint the complete source-root file manifest, including hidden and VCS-named files; support live default selection and complete fingerprint pins without invented branches or retained history.
- [ ] Extract files/directories at the requested complete fingerprint, detect inconsistent preparation, reject source/destination overlap, and distinguish unavailable old states from absent source paths.
- [ ] Test local/file-URL identity, authority and query restrictions, exact bytes, changes outside mapped paths, empty directories, symlinks, concurrent source changes, and an unusable cache override; complete the common review gate.

## Stage 20 — Repository and dependency-source input parsing

Relevant contracts: [dependency definitions and source arguments](../behavior/cli.md#dependency-definitions-and-source-arguments) and [repository identity](../behavior/vcs-interaction.md#repository-locators-and-identity).

- [ ] Parse VCS-qualified/native URLs, local paths, scp-style addresses, explicit backend assertions, and invocation-relative repository locations into typed intent.
- [ ] Parse typed/bare URL fragments and option aliases, enforcing selector exclusivity, exactly-once percent decoding, preserved `+`, and native literal meaning for non-URL locators.
- [ ] Parse `dependency://NAME/PATH`, including root selection, without accepting user information, ports, query strings, fragments, raw repositories, or per-entry version selection.
- [ ] Test ambiguous/unsupported source notation, plain HTTP guidance, `.git` suffix non-inference, malformed escapes, option conflicts, and equivalent inputs; leave reference-kind inference requiring repository facts to the source layer; complete the common review gate.

## Stage 21 — Reusable native-client execution

Relevant contracts: [external integrations](../architecture/modules_layout.md#external-integrations), [native execution](../behavior/vcs-interaction.md#native-client-execution), and [errors](../architecture/errors.md).

- [ ] Add reusable native-process execution at the appropriate technical boundary, using argument lists, captured output, and explicit noninteractive operation.
- [ ] Preserve native authentication, configured identity, and policy; convert known launch/nonzero failures into typed operational diagnostics while leaving programming errors as exceptions.
- [ ] Keep client stdout/stderr out of the protocol stream and ensure locators, literal paths, and selectors cannot become shell commands or unintended client options.
- [ ] Test unavailable executables, failed/successful invocations, literal unusual arguments, retained causes/context, and protocol-stream isolation; complete the common review gate.

## Stage 22 — Cache roots and owned repository storage

Relevant contracts: [cache-root selection](../behavior/cache.md#cache-root-selection), [repository identity](../behavior/cache.md#repository-identity-and-layout), and [cache ownership](../behavior/cache.md#cache-ownership).

- [ ] Add invocation-local `--cache-dir` through the integration decided in Stage 1, deferring resolution and validation until an operation needs the cache.
- [ ] Implement explicit/default/fallback root selection, home expansion, invocation-relative resolution, and one stable effective absolute root per invocation.
- [ ] Establish repository storage identity and ownership under `repos/<vcs>/<repo-name>`, distinguishing complete normalized locators while sharing storage across dependencies, selectors, projects, and hosting choices.
- [ ] Test absent roots, unusable explicit/default roots without fallback, basename collisions, unrelated content conflicts, containment, incomplete owned data, and cache-independent commands with unusable overrides; complete the common review gate.

## Stage 23 — Exclusive cache access

Relevant contracts: [cache access locking](../behavior/cache.md#cache-access-locking).

- [ ] Implement one exclusive `<cache-root>/cache.lock` boundary used by every cached read/write, with independent roots and the specified 10-second acquisition timeout.
- [ ] Hold protection through native subprocess access and release it on normal completion, failure, and process termination without treating a persistent lock file as a held lock.
- [ ] Preserve the lock file and existing root across release; do not create or acquire them for cache-independent operations.
- [ ] Test contention with independent processes, acquisition after another owner exits, timeout with no protected mutation, failure/termination release, and stable coordination-file identity; complete the common review gate.

## Stage 24 — Cache cleanup through `cache clean`

Relevant contracts: [cache cleanup](../behavior/cache.md#cleanup), [cache commands](../behavior/cli.md#vcsinstall-cache-command-group), and [cache records](../behavior/cli.md#cache-records).

- [ ] Remove only established vcsinstall-owned disposable data across repositories and backends while holding the common exclusive cache lock.
- [ ] Preserve unrelated content, original sources, installed/project files, the cache root, and the lock file; do not follow symlinks outside the cache.
- [ ] Register the cache group and cleanup runtime/logic cell, including unchanged success for absent/empty roots and truthful partial-cleanup diagnostics.
- [ ] Test cleanup contention, unrelated/symlink content, absent-root noncreation, all protocols, bare group help, rejected arguments, and operation outside a project with unusable `--config`; complete the common review gate.

## Stage 25 — Git revision interpretation

Relevant contracts: [Git sources](../behavior/vcs/git.md#version-selectors).

- [ ] Implement Git source interpretation of supplied local objects and observed metadata, with exact branch/tag namespaces and advertised default-branch inference.
- [ ] Support lightweight/annotated commit tags, complete SHA-1/SHA-256 commits where the client supports them, and unambiguous CLI abbreviations normalized to complete commit pins.
- [ ] Reject branch/tag ambiguity, symbolic immutable pins, non-commit objects, unavailable/default metadata, and deleted refs without transport or fallback resolution.
- [ ] Test with real temporary Git repositories and controlled hosting observations, including nonstandard defaults, moved refs, opaque literal ref names, and client-supported object formats; complete the common review gate.

## Stage 26 — Exact Git content extraction

Relevant contracts: [reading committed content](../behavior/vcs/git.md#reading-committed-content) and [source extraction](../behavior/vcs-interaction.md#source-inspection-and-extraction).

- [ ] Read selected committed trees and blob bytes from available local Git data without fetching, modifying the original repository, or using working-tree/index membership.
- [ ] Preserve literal paths and binary bytes without line-ending conversion, filters, archive attribute transformations, or implicit LFS payload retrieval; retain supported executable metadata separately from identity.
- [ ] Distinguish absent paths from missing objects, and reject selected symbolic links, submodules, unsafe names, and incomplete trees without rejecting unrelated supported paths.
- [ ] Test real Git extraction with attributes, ignored/untracked/modified files, binary data, unusual filenames, unsupported objects, and unavailable commits; complete the common review gate.

## Stage 27 — Git acquisition and refresh

Relevant contracts: [Git acquisition](../behavior/hosting/git.md#repository-acquisition-and-refresh), [cache reuse](../behavior/cache.md#repository-reuse-and-revision-isolation), and [source freshness](../behavior/locking.md#source-freshness).

- [ ] Implement Git hosting clone/fetch and advertised reference/default-branch observations through native clients under the cache ownership and locking boundaries.
- [ ] Reuse valid immutable data, acquire missing required objects, and refresh current branch/tag observations without letting stale or deleted cached refs satisfy freshness.
- [ ] Preserve original local sources and keep different locked revisions independently retrievable from shared repository storage; incomplete acquisition must not become a valid view.
- [ ] Test real acquisition/refresh against temporary local repositories, moved/deleted refs and defaults, unavailable objects, cache rebuild, failed transport, independent dependencies, and complete independence from `gh`; complete the common review gate.

## Stage 28 — Runtime source preparation and freshness

Relevant contracts: [locking](../behavior/locking.md), [acquisition](../behavior/vcs-interaction.md#repository-acquisition-and-refresh), and [anchoring](../behavior/vcs-interaction.md#version-resolution-and-anchoring).

- [ ] Coordinate operation-specific acquisition, refresh, source resolution, and immutable snapshot preparation through the independent adapters, invoking only required capabilities.
- [ ] Resolve a selected revision once per dependency for an invocation and supply it to every affected entry, including dependencies without entries and unchanged resolutions.
- [ ] Implement current-versus-locked comparisons, retained inferred branches, immutable-pin behavior, and complete source-integrity checks when reproducing an existing baseline.
- [ ] Test with independently controlled adapters plus real filesystem/Git paths: failed refresh, equal-content/different-revision freshness, unavailable historical states, cache rebuild, and no acquisition when trustworthy installed state already satisfies a source-independent operation; complete the common review gate.

## Stage 29 — Applying prepared project operations

Relevant contracts: [preparation and consistency](../behavior/locking.md#preparation-and-consistency), [lockfile writes](../behavior/lockfile.md#writes-and-failures), and [filesystem effects](../behavior/files-and-directories.md#filesystem-effects).

- [ ] Add runtime application of fully prepared project changes through the existing project, filesystem, and workspace boundaries, with selection-wide checks completed before mutation.
- [ ] Order installed-content and metadata effects so prior baselines remain authoritative until all affected content is successfully applied; do not record a partial installation as complete.
- [ ] Preserve unrelated declarations, locks, and content; detect concurrent metadata changes and report known partial effects rather than implying rollback when application fails.
- [ ] Test multiple dependencies and entries, late preparation failure, injected file/configuration/lockfile write failures, concurrent edits, and byte-preserving no-ops through the runtime boundary; complete the common review gate.

## Stage 30 — Declaring and redefining dependencies

Relevant contracts: [`dependency add`](../behavior/cli.md#vcsinstall-dependency-add-command) and its [locking contract](../behavior/locking.md#dependency-add).

- [ ] Implement runtime declaration/locking and register `dependency add` with all source forms, name validation, and explicit `--replace` behavior.
- [ ] Record inferred Git default branches and resolved selector kinds in configuration, lock dependencies without entries, and refresh every existing entry during dependency redefinition using the established protection rules.
- [ ] Emit dependency records followed by affected entry records, with correct outcomes, shared revisions, and previous revisions for replacements.
- [ ] Test new/duplicate/identical/replaced definitions, branch/tag/pin inference and ambiguity, changed defaults, preparation failure preservation, zero-entry locks, all protocols, and both production backends; complete the common review gate.

## Stage 31 — Declaring and installing one entry

Relevant contracts: [`add`](../behavior/cli.md#vcsinstall-add-command), [entry addition](../behavior/locking.md#add), and [installation protection](../behavior/locking.md#installation-protection).

- [ ] Implement and register `add SOURCE DESTINATION`, installing exact file/directory/root selections at an already matching dependency lock without advancing that lock.
- [ ] Implement `--replace` and `--allow-outdated` as independent permissions, including entry reassignment and old-baseline protection, with all command/selection checks before source access.
- [ ] Check freshness before mutation, fail absent initial source paths, and retain revision-availability/integrity checks even when outdated use is permitted; emit the complete installation result.
- [ ] Test renamed destinations, empty directories, duplicate mappings, stale/missing locks, outdated versus failed freshness, unavailable filesystem history, identical replacements, collisions, missing-file restoration, and unselected-state preservation; complete the common review gate.

## Stage 32 — Declared installation and locked restoration

Relevant contracts: [`install`](../behavior/cli.md#vcsinstall-install-command) and [installation state transitions](../behavior/locking.md#install).

- [ ] Implement and register ordinary installation across every declared dependency and entry, reusing matching locks and resolving only missing/changed bindings, including dependencies without entries.
- [ ] Implement `--locked` with complete configuration-match validation before effects and byte-for-byte preservation of the lockfile; allow required immutable acquisition without promising offline execution.
- [ ] Restore missing selected files after protecting all existing content, verify reproduced source manifests, remove obsolete lock records only after success, and never delete content solely because its declaration was manually removed.
- [ ] Test nested directory restoration, manually changed declarations, source-independent satisfied installations, unavailable locked content, new entries using old locks, multi-dependency preparation failure, repeated no-ops, and ordered records; complete the common review gate.

## Stage 33 — Updating selected dependencies

Relevant contracts: [`dependency update`](../behavior/cli.md#vcsinstall-dependency-update-command) and [updating baselines](../behavior/locking.md#updating-baselines).

- [ ] Implement and register all/named dependency selection, deduplication, unknown-name rejection, and revision refresh/resolution once for each selected dependency.
- [ ] Prepare every declared entry of selected dependencies together, preserving requested configuration/selectors and unselected state while supporting source additions, deletions, and expected-absence transitions.
- [ ] Apply the complete old-versus-proposed equality protection, including accepted upstream-equivalent local content, restorable missing files, and failure on untrustworthy baselines.
- [ ] Test rewound branches, moved/deleted tags, pins, empty dependencies, old bindings after manual changes, directory membership changes, whole-selection failure preservation, prior/current revision records, and the absence of a root `update` alias; complete the common review gate.

## Stage 34 — Reporting outdated dependencies

Relevant contracts: [`dependency outdated`](../behavior/cli.md#vcsinstall-dependency-outdated-command) and [source freshness](../behavior/locking.md#source-freshness).

- [ ] Implement and register all/named checks using matching dependency locks, validating every selected lock before source fetching and without requiring installed-content verification.
- [ ] Emit only differing complete locked/current revision pairs, including equal-content changes and non-descendant moves, while treating immutable pins according to their freshness contract.
- [ ] Keep project configuration, lockfile bytes, and installed content unchanged while permitting required cache population/refresh.
- [ ] Test duplicates/unknown names, stale/missing locks, empty/no-outdated success, failed refresh, dependencies without entries, filesystem changes outside mapped paths, and output ordering/protocols; complete the common review gate.

## Stage 35 — Removing entries

Relevant contracts: [`remove`](../behavior/cli.md#vcsinstall-remove-command), [entry removal](../behavior/locking.md#remove), and [recorded ownership](../behavior/files-and-directories.md#filesystem-effects).

- [ ] Implement and register explicit multi-entry removal with all selector, metadata, and required content checks before mutation; emit removal records rather than post-removal installation statuses.
- [ ] Support ordinary verified deletion, already absent content, and `--force` waiving verification without granting ownership of unmanaged descendants or bypassing invalid metadata/paths.
- [ ] Support `--keep-files`, taking precedence over force, without content verification; retain dependency declarations/shared locks after removing their last entry and avoid creating an absent lockfile for record-only work.
- [ ] Test missing baselines, structurally required directory manifests, unmanaged files, modified/partially missing content, cross-dependency failure preservation, repeated selectors, no implicit selection, and no cache/client/source access; complete the common review gate.

## Stage 36 — Removing unused dependencies

Relevant contracts: [`dependency remove`](../behavior/cli.md#vcsinstall-dependency-remove-command) and [dependency removal](../behavior/locking.md#dependency-remove).

- [ ] Implement and register removal of one unused dependency and its lock/obsolete entry records, preserving installed files and unrelated metadata.
- [ ] Refuse dependencies still owning declared entries with useful guidance, without a cascade or force bypass.
- [ ] Preserve absent-lockfile noncreation, validate existing metadata, and emit the observed pre-removal definition/revision in its dependency record.
- [ ] Test unknown/used/unused dependencies, missing locks, obsolete records, malformed metadata, output protocols, and complete source/cache/client independence; complete the common review gate.

## Stage 37 — Research contribution preparation and refine delivery stages

Relevant contracts: [contributions](../behavior/files-and-directories.md#contributions), [publication](../behavior/vcs-interaction.md#contribution-publication), and all [hosting contracts](../intro.md).

- [ ] Review the implemented snapshots, adapters, cache, and runtime boundaries; define the minimal typed contribution and delivery data needed without adding another publication-adapter layer or leaking project models into adapters.
- [ ] Work out exact Git tree/commit preparation, path-overlap handling, executable modes, native patch application, conditional ref publication, local-recipient protection, and recovery evidence after uncertain effects.
- [ ] Confirm the `gh` operations required for exact host/account/source identity, fork eligibility, target ancestry, and review matching; design controlled service-boundary tests without network access.
- [ ] Refine or split Stages 38–50 based on concrete interfaces, and schedule behavior-specific noop delivery increments alongside the real modes they verify; retain independent native/service integration coverage.
- [ ] Record decisions and any separately authorized dependency/client prerequisites in the plan; complete the common review gate without changing current command behavior.

## Stage 38 — Complete local contribution selection

Relevant contracts: [contributions](../behavior/files-and-directories.md#contributions) and [push baselines](../behavior/locking.md#dependency-push).

- [ ] Extend filesystem inspection with an explicit complete-tree contribution scope, distinct from baseline-scoped status, including hidden/generated/new files and missing-file deletions.
- [ ] Implement project/runtime selection of changed entries within one named dependency, or exact explicitly selected roots, requiring matching trustworthy baselines only for entries actually inspected.
- [ ] Map selected content back to source paths and reject conflicting overlapping source changes while preserving source content outside selected mappings; combine only compatible overlaps.
- [ ] Test clean ordinary status with new contribution files, type changes, absent roots, unreadable/unsupported objects, foreign-dependency entries, unselected unreadable content, overlap conflicts, and no source requests for an unchanged selection; complete the common review gate.

## Stage 39 — Push request validation and commit messages

Relevant contracts: [`dependency push`](../behavior/cli.md#vcsinstall-dependency-push-command), [delivery modes](../behavior/vcs-interaction.md#delivery-modes), and [destination selection](../behavior/vcs-interaction.md#publication-destination-selection).

- [ ] Add typed push intent and mode/capability validation, including backend-consistent publication overrides without version fragments and explicit dependency/entry membership.
- [ ] Enforce the mode-specific option matrix before reading message files/stdin or contribution payloads, fetching sources, or invoking hosting services; do not register an incomplete push command yet.
- [ ] Resolve repeated message paragraphs, UTF-8 message files and stdin, invocation-relative paths, mutually exclusive message forms, and nonempty noninteractive defaults for commit-producing modes.
- [ ] Test invalid/unsupported combinations, publication locator identity, malformed options, file/stdin errors, default messages, overwrite exclusions, and validation-before-read ordering; complete the common review gate.

## Stage 40 — Native Git contribution commit preparation

Relevant contracts: [Git change application](../behavior/hosting/git.md#change-application-and-commit-metadata) and [contribution preparation](../behavior/hosting/git.md#contribution-preparation).

- [ ] Prepare one Git commit at a supplied base with selected exact source-path changes, preserving unselected tree content and existing executable modes and taking new-file executable status into account.
- [ ] Support file/directory additions, replacements, type changes, and deletions without filters or unrelated worktree/index changes; reject unrepresentable changes before delivery.
- [ ] For branch/patch/PR contributions, use the locked commit as the parent independently of current source refs; honor native commit identity/signing and message behavior without editing target or original source state.
- [ ] Test real temporary repositories, binary bytes, unusual paths, overlapping selections, modes, advanced upstream branches, identity/policy failures, and unavailable locked bases; complete the common review gate.

## Stage 41 — Protected patch artifact writes

Relevant contracts: [patch output](../behavior/cli.md#patch-output) and [patch artifacts](../behavior/vcs-interaction.md#patch-artifacts).

- [ ] Resolve explicit output files relative to invocation cwd after home expansion, rejecting `-`, project notation, directories, symlinks, and overlap with project metadata, declared destinations, cache data, or native source administrative data.
- [ ] Add atomically visible artifact replacement after successful preparation, preserving an existing regular file or initial absence on preparation/replacement failure.
- [ ] Keep artifact storage user-managed and separate from project state and stdout; an empty changed selection must leave its artifact untouched.
- [ ] Test allowed unmanaged-file replacement, all protected overlaps, parent/target failures, relative paths, existing artifacts, and unchanged selections; complete the common review gate.

## Stage 42 — Patch delivery through `dependency push`

Relevant contracts: [Git patch delivery](../behavior/hosting/git.md#patch-delivery) and [push results](../behavior/cli.md#installation-records).

- [ ] Implement Git hosting generation of one native `format-patch` mailbox artifact with binary changes, complete blob identifiers, commit metadata, and locked-base information.
- [ ] Register the complete patch-mode path through `dependency push`, advertising only implemented modes/options and retaining the shared command/error/output boundary.
- [ ] Add independently supplied noop patch delivery with controlled bytes through the same artifact-writing boundary; construct complete exported/unchanged result fields without changing configuration, lockfile bytes, or installed content.
- [ ] Test native `git am` application in a temporary repository, exact bytes and source paths, all protocols, no publication or `gh` requirement, empty/unchanged selections, and generation/write failure preservation; complete the common review gate.

## Stage 43 — Contribution branch delivery

Relevant contracts: [Git branch delivery](../behavior/hosting/git.md#branch-delivery), [local safeguards](../behavior/hosting/git.md#local-publication-safeguards), and [push results](../behavior/cli.md#installation-records).

- [ ] Add native Git branch delivery to the source or explicit recipient, using a valid explicit/generated name and conditional ref creation that cannot advance any existing branch.
- [ ] Enforce the resolved recipient/ref despite client push defaults and preserve local recipients' worktrees, indexes, configuration, and checked-out branches; reject the target project's own Git repository.
- [ ] Expose branch mode and extend noop delivery with persistent fixture branches, collision injection, and known/uncertain publication effects; report actual recipient, branch, and contribution revision.
- [ ] Test real local bare/non-bare recipients, different publication repositories, concurrent branch creation, explicit and automatic names, permission failures without fork fallback, and project-state byte preservation; complete the common review gate.

## Stage 44 — Direct Git delivery preparation and protection

Relevant contracts: [direct destination and content protection](../behavior/hosting/git.md#direct-delivery).

- [ ] Resolve the existing recipient branch from an explicit name or effective branch selector, requiring an explicit branch for tag/revision dependencies.
- [ ] Inspect its current complete revision B and compare all selected complete contribution scopes with local content and recorded baselines, without requiring the old locked source commit.
- [ ] Prepare changes only when recipient content equals the baseline, accept already-present local content without a write, and fail other differences before commit creation; preserve all unrelated content from B.
- [ ] Test source-only directory additions, independent remote/local edits, mixed already-present/writable entries, type obstructions, missing branches, unavailable old source commits, and absence of automatic merge/rebase; complete the common review gate.

## Stage 45 — Conditional direct Git publication

Relevant contracts: [direct completion](../behavior/hosting/git.md#commit-and-completion) and [completed effects](../behavior/vcs-interaction.md#failures-and-completed-effects).

- [ ] Publish a prepared direct commit with parent B only if the existing destination branch still equals B, allowing only that branch's fast-forward advancement and no silent retry at a different head.
- [ ] Reject branches checked out in any worktree of a local recipient and preserve local/target repository safeguards without bypassing native receiving policies.
- [ ] Expose direct mode and extend noop simulations for concurrent movement, conflicts, and already-present content; report verified already-present delivery as pushed at B without creating a commit.
- [ ] Test real local ref races, deleted/recreated destinations, no-write retries, receiving failures, correct outcomes, and unchanged project files/baselines; complete the common review gate.

## Stage 46 — Explicit filesystem overwrite delivery

Relevant contracts: [filesystem hosting](../behavior/hosting/filesystem.md#overwrite-delivery) and [complete contribution scope](../behavior/files-and-directories.md#contributions).

- [ ] Implement filesystem hosting writes of selected complete local mappings into the original source, including source-only child removal and authorized type changes, while preserving all content outside selected mappings.
- [ ] Require explicit overwrite mode, retain source containment/permissions/object checks, and never delete the source root or replace it with a file; do not require the locked historical fingerprint to remain available.
- [ ] Verify resulting selected content and the complete resulting source fingerprint, expose overwrite mode, and extend noop overwrite against explicit fixture state without real source writes.
- [ ] Test changed live sources, new files, deletions, mapping `.`, overlaps, partial write failures, unusable cache overrides, unchanged installation state, and subsequent update acknowledgement; complete the common review gate.

## Stage 47 — GitHub hosting identity and native delegation

Relevant contracts: [GitHub availability and identity](../behavior/hosting/github.md), [hosting selection](../behavior/vcs-interaction.md#hosting-selection-and-capabilities), and [external integrations](../architecture/modules_layout.md#external-integrations).

- [ ] Add GitHub hosting composition over Git acquisition/native delivery, preserving native operations' independence from `gh`, API login, and hosted-service discovery.
- [ ] Implement PR-specific `gh` integration for exact source repository, host, and configured account identity, with GitHub.com inference and explicit/configured enterprise-host or SSH-alias resolution.
- [ ] Convert controlled service-response and authentication/client failures through the hosting boundary without substituting fork-network roots, a default API host, or commit-author identity.
- [ ] Test native delegation with unavailable `gh`, service identity through controlled client responses, custom hosts, ambiguous aliases/accounts, malformed responses, and no interactive authentication; complete the common review gate.

## Stage 48 — GitHub review target and recipient eligibility

Relevant contracts: [PR targets](../behavior/hosting/github.md#pr-target-selection) and [publication repositories](../behavior/hosting/github.md#publication-repository-and-forks).

- [ ] Resolve the source review branch from `--base` or the effective branch selector, requiring an explicit base for tags/pins and checking the exact existing source branch.
- [ ] Obtain enough history to prove locked-base ancestry before any fork creation or publication; keep the contribution parent locked rather than rebasing to the review target.
- [ ] Use host/account permissions and fork metadata to validate an explicit recipient or choose the writable source/eligible configured-account fork, rejecting ambiguity and ineligible or cross-service repositories.
- [ ] Test incomplete history, incompatible branches, declared fork sources, unrelated same-basename repositories, multiple eligible forks, forbidden access, and validation-before-mutation with real local Git plus controlled service responses; complete the common review gate.

## Stage 49 — GitHub fork preparation

Relevant contracts: [publication repositories and forks](../behavior/hosting/github.md#publication-repository-and-forks) and [completed effects](../behavior/vcs-interaction.md#failures-and-completed-effects).

- [ ] Implement reuse or permitted creation of an eligible configured-account fork only as part of requested PR delivery, after the full contribution and target have passed preparation.
- [ ] Ensure the chosen/new fork is available for the intended Git publication while sourcing contribution objects from the original source, even for default-branch-only forks.
- [ ] Retain explicit-recipient selection, permission/policy diagnostics, and known fork effects on later failure; do not infer fork need from a transport failure or clean up completed remote effects automatically.
- [ ] Extend independent noop fork state and test reuse, creation policy, readiness failures, recipient ambiguity, object availability, and failure recovery through controlled service responses; complete the common review gate.

## Stage 50 — PR delivery, review recovery, and completion

Relevant contracts: [review creation and retry](../behavior/hosting/github.md#review-creation-retry-and-completion) and [publication completion](../behavior/vcs-interaction.md#failures-and-completed-effects).

- [ ] Implement the complete PR workflow over prepared Git content and the resolved source/head/base, with noninteractive review title/body and no merge action.
- [ ] Allow explicit-branch retry only after verifying parent, complete tree, and message; preserve conditional branch absence for new publication and refuse conflicting existing contributions.
- [ ] Find and return exactly matching open reviews, create only when appropriate, report closed/merged/conflicting/ambiguous states, and require a confirmed matching review URL for completed changed-content delivery.
- [ ] Expose PR mode and complete noop review/retry/failure fixtures, including lost responses and persistent known/uncertain effects, without project publication history or implicit source-fixture mutation.
- [ ] Test complete workflows with real Git preparation and controlled `gh` responses, retries after cache cleanup, branch/review mismatch, late failures retaining recovery context, all mode-specific result fields, and unchanged project installation state; complete the common review gate.

## Stage 51 — Final specification and installed-package review

Relevant contracts: [CLI](../behavior/cli.md), [test architecture](../architecture/tests.md), [README](../documentation/readme.md), [skill fixtures](../behavior/skill_fixtures.md), and [changelog](../documentation/changelog.md).

- [ ] Audit every owning specification against implemented boundaries and tests, recording any uncovered contract as its own small follow-up stage before declaring the plan complete.
- [ ] Review all exposed commands, help/completion, supported backend/hosting/mode combinations, default/explicit protocols, deterministic ordering, null effect fields, warnings/errors, and repeated invocation isolation; retain the absence of root `update`/`push` aliases and production noop access.
- [ ] Reconcile README and packaged usage with the completed scope, including directory ownership versus complete-tree contribution selection, filesystem history limits, locked restoration, and noninteractive contribution modes; regenerate the changelog from its maintained sources.
- [ ] Run the full Donna polish workflow and installed-wheel runtime check through repository helpers; verify packaged initialization and a temporary-project installation/inspection workflow outside the source tree.
- [ ] Provide the final human-review handoff with stage completion, validation evidence, and any explicitly remaining limitations; complete the common review gate.
