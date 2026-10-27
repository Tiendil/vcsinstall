# Source and hosting interaction

## Goal of the document

This document describes repository acquisition and refresh, source interpretation and extraction, and explicit delivery of installed changes.

## Scope

This specification covers inputs, source data, revision resolution, external effects, and failures at source and hosting boundaries.
The following are out of scope:

- CLI parsing and result serialization.
- configuration and lockfile schemas.
- installation-baseline transitions and content-hash computation.
- cache layout and access locking.
- Python interfaces and VCS wire protocols.
- adapter-specific delivery and review details beyond the applicable hosting contract.

## Contract boundaries

Terminology MUST follow `specs/dictionary.md`.
Architectural responsibilities and integration choices MUST follow `specs/architecture/modules_layout.md`.
Repository arguments and source paths MUST follow `specs/behavior/cli.md`; persisted declarations MUST follow `specs/behavior/config.md`.
Revision selection, baseline verification, and project state changes MUST follow `specs/behavior/locking.md`.
Persisted revision and content identities MUST follow `specs/behavior/lockfile.md`.
Source and hosting access using the repository cache MUST follow `specs/behavior/cache.md`.

Test-only adapters MUST be available only when explicitly supplied by a test environment under `specs/architecture/tests.md`.
Normal application execution MUST reject them as unsupported, without a production CLI option that enables them.
They MUST be supplied independently; selecting one MUST NOT implicitly enable another source or hosting adapter.

## Backend selection and capabilities

The supported production source backends MUST be:

- `git` — Git repositories, under `specs/behavior/vcs/git.md`.
- `filesystem` — Ordinary filesystem directories, under `specs/behavior/vcs/filesystem.md`.

Tests MAY explicitly supply the test-only `noop` source under `specs/behavior/vcs/noop.md`, independently of hosting selection.

Backend selection MUST be explicit through the normalized declaration produced under the CLI repository-locator rules.
An unknown or unsupported backend MUST fail explicitly without falling back to Git or ordinary filesystem access.
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
- contribution preparation.
- delivery effects or artifact creation.

Unsupported transports or selectors MUST fail explicitly rather than be ignored or reinterpreted as another capability.
Unsupported command options MUST be rejected under the CLI's invalid-argument contract before source access or external mutation.
Read access MUST NOT imply permission to publish.
Ordinary filesystem and HTTP source notation MUST NOT by itself imply VCS revision history or contribution-branch support.

Additional VCS support MUST preserve native semantics rather than impose Git's branch model on every backend.
For example, Mercurial may support tags and changeset identifiers, while SVN revisions identify repository states and branch or tag locations are ordinary source paths.
Corresponding locator forms can include `hg+https://example.com/shared#tag=v1.0`, `hg+file:///shared#rev=CHANGESET_HASH`, or `svn+https://example.com/shared#rev=1234` when those backends are supported.
SVN support MUST NOT assume a `trunk`, `branches`, or `tags` layout or invent native named branch selectors.
Additional selector kinds, such as Mercurial bookmarks, MUST have explicit input and result semantics before being exposed.

## Hosting selection and capabilities

The supported production hosting implementations MUST be:

- `git` — Native Git acquisition, refresh, and delivery, under `specs/behavior/hosting/git.md`.
- `github` — Delegated Git acquisition and native delivery, with GitHub PR workflows, under `specs/behavior/hosting/github.md`.
- `filesystem` — Access to an ordinary source directory and authorized direct writes, under `specs/behavior/hosting/filesystem.md`.

Tests MAY explicitly supply test-only `noop` hosting under `specs/behavior/hosting/noop.md`, independently of source-backend selection.

A source hosted on GitHub MUST retain backend `git`; hosting selection MUST NOT change its native repository locator or revision meaning.
Hosting selection MUST NOT create another source entity or change repository-cache identity or lock matching.
The CLI's `dependency push --hosting-provider` option MUST select the hosting implementation for that invocation, including any required acquisition; an explicit value MUST override inference.

Acquisition without explicit hosting selection MUST use `git` for Git sources and `filesystem` for filesystem sources.
Acquiring a Git repository MUST NOT require inference of its hosting service or service API authentication.
Acquisition and refresh MUST NOT require a delivery mode or publication permission.

Without an explicit value, Git branch, direct, and patch delivery MUST select `git` regardless of the source's hosting service.
Filesystem overwrite delivery MUST select `filesystem`.
PR delivery MUST select a compatible service-specific implementation from an unambiguous recognized source host or native-client configuration.

Unknown implementations and unsupported hosting/source/mode combinations MUST fail under the CLI's invalid-argument contract before payload reads or source access.
Where service identification is required, unresolved or ambiguous identity MUST fail with guidance to select or configure hosting before external mutation.
Custom GitHub hosts and SSH aliases MUST be resolved unambiguously rather than routed to an unrelated default API host; a Git locator MUST NOT alone imply GitHub.

Each hosting implementation MUST define its compatible source kinds and acquisition guarantees.
Its delivery contract MUST define supported modes, destination rules, and external effects.
Native acquisition and delivery MUST remain usable without service-specific clients or API authentication.
Service authentication and account discovery failures MUST remain explicit operational failures rather than trigger interactive login or another account selection.
Additional implementations MUST define acquisition and refresh behavior, supported delivery, and applicable review or fork capabilities before being exposed.

## Native client execution

Client selection and protocol integration MUST follow External integrations in `specs/architecture/modules_layout.md`.
A required but unavailable client MUST cause an operational failure; vcsinstall MUST NOT install it automatically.
Authentication and commit-identity failures MUST remain operational diagnostics.
Filesystem and noop source or hosting access MUST NOT require or invoke a VCS executable.

Client invocations MUST preserve native locator, transport, and authority semantics under Repository locators and identity in `specs/behavior/cli.md`.
Repository locators, selectors, and paths MUST be passed as data without allowing them to become shell commands or unintended client options.
Paths MUST retain their literal meaning even when a native client ordinarily interprets them as patterns or revision syntax.
Native client output MUST follow the CLI's protocol-delivery contract rather than be copied directly into the result stream.

Source inspection MUST NOT alter an original repository's working files or administrative state.
Cache changes MUST remain separate from original source and target project state.
Only a separately authorized hosting workflow MAY publish changes to a recipient under its delivery contract.

## Repository acquisition and refresh

Acquisition MUST make repository data and version metadata available for source resolution and extraction under the requested operation's revision and freshness requirements in `specs/behavior/locking.md`.
A repository view MUST identify the declared backend and normalized repository locator; data for a different identity MUST NOT satisfy the request.
Where applicable, its metadata MUST distinguish observed branch and tag targets from the source's advertised default branch.
Acquisition or refresh MUST NOT itself choose a new installation revision or change project declarations, lock state, or installed content.

Valid available data MAY be reused when it satisfies the operation's requirements.
When current source information is required, refresh MUST obtain the required observations from the declared source; failed refresh MUST NOT be satisfied by stale cached references.
Acquisition of a locked immutable revision MUST NOT require adopting the current branch or default revision.
Missing required objects or metadata MUST be acquired through hosting or reported as an operational failure; source inspection MUST NOT trigger implicit transport access.
An available view MUST NOT imply complete history, retained snapshots, or future availability of every revision.

Cached acquisition and reuse MUST follow `specs/behavior/cache.md`.
An uncached hosting implementation MUST define how the declared source becomes available without requiring the cache.
Acquisition MUST preserve the original source's content and administrative state; only authorized delivery MAY change the recipient under its mode contract.

## Version resolution and anchoring

A successful resolution MUST identify one complete immutable revision for the selected repository state.
The identifier MUST keep the same content meaning across invocations and cache locations; it MUST NOT depend on a temporary checkout path or invocation timestamp.
An identifier MUST NOT imply that historical content remains available forever.

Named selectors MUST remain distinct from their resolved immutable revisions.
Default-version inference and selector recording MUST follow Version selection in `specs/behavior/locking.md` and the backend's own contract.
An unversioned source MAY define its default as the current live content state; it MUST NOT invent a branch or tag to represent that state.
Bare-selector ambiguity MUST fail under the CLI contract rather than use a client's shorthand precedence.

Resolution MUST interpret the available view and supplied version metadata; current-source checks MUST use observations satisfying Source freshness in `specs/behavior/locking.md`.
Source content prepared for one dependency operation MUST belong to the revision selected for that operation under State and shared revisions in that specification.
A source changing during preparation MUST NOT cause mixed revisions to be installed or recorded as one revision.

## Source inspection and extraction

Inspection and extraction MUST use the requested immutable revision from the available view or fail operationally; they MUST NOT silently substitute a newer, older, or default revision.
Cached repository views MUST follow Repository reuse and revision isolation in `specs/behavior/cache.md`.
An uncached backend MUST define how a requested revision is verified against available source content without requiring the cache.

Extraction MUST distinguish:

- a regular file with its exact bytes.
- a directory with its complete selected content tree.
- an absent path at an otherwise available revision.
- an unavailable revision or failed inspection.

An inspection or retrieval failure MUST NOT be reported as source-path absence.
Whether absence can establish or update an entry MUST follow `specs/behavior/locking.md`.
Extraction MUST preserve literal names and file bytes without applying package discovery or application-specific layout rules.
The backend MUST identify which repository data belongs to the content tree and which administrative data is excluded.

Unsupported source objects or names that cannot be represented safely at the destination MUST fail before project mutation rather than be omitted, followed, or renamed silently.
Source paths MUST remain within the selected content root; symbolic links MUST NOT bypass source or destination containment.
Prepared source content MUST satisfy Content identity and Source integrity and cache independence in `specs/behavior/locking.md`.

## Contribution publication

Publication selection and local verification MUST follow the `dependency push` contracts in `specs/behavior/cli.md` and `specs/behavior/locking.md`.
Each invocation MUST deliver changes from exactly one declared dependency through exactly one explicit delivery mode.
The source repository MUST remain the authority for installed versions; the publication repository MAY differ under Publication destination selection below.
Selected changes MUST map installed destinations back to their recorded source paths, including selected additions, replacements, and deletions.
Selected mappings proposing conflicting content or deletion at the same source path MUST fail before creating a contribution or applying source writes.
Compatible overlapping edits MAY be combined, but unselected entries MUST NOT contribute local changes.

Contribution bases and revision requirements MUST follow the `dependency push` operation contract in `specs/behavior/locking.md` and the selected hosting contract.
The resulting contribution MUST reproduce the selected changes at their source paths while preserving unrelated content from its applicable base.
A source root or content kind that cannot be represented by the selected hosting implementation MUST cause an explicit failure rather than an inaccurate successful publication.
A selection without changed content MUST complete without source fetching, hosting requests, or delivery effects once local selection and option validation passes.
All required source preparation and selected-content checks MUST finish before creating a fork or applying delivery effects.

Creation of a contribution branch MUST protect existing branches from overwrite, including collisions occurring during publication.
PR retry MAY reuse a verified matching contribution under the hosting contract, but MUST NOT overwrite a conflicting existing branch.
Direct mode MUST protect the observed destination revision and conflicting source content under its contract; selecting it MUST NOT authorize history rewriting.
Delivery results MUST identify the requested mode and its actual completed effects under the CLI output contract.

Publication MUST preserve project state under the `dependency push` operation contract in `specs/behavior/locking.md`.
Acknowledging accepted content on a later update MUST follow Updating baselines in that specification without recording publication history.

## Delivery modes

The explicit `--mode` values MUST be as follows; required outcomes apply to changed content, while unchanged selections follow Contribution publication above.

| Mode | Required outcome | Required capabilities |
| --- | --- | --- |
| `pr` | Publish a contribution branch and create or recover its hosted review request. | Compatible repository publication and hosted review support. |
| `branch` | Publish a contribution branch without creating a hosted review. | Native contribution-branch publication. |
| `direct` | Apply selected changes to an existing destination branch, or confirm they are already present. | Native conditional branch advancement with baseline protection. |
| `patch` | Write one contribution patch artifact without remote publication. | Contribution patch generation. |
| `overwrite` | Apply selected changes directly to the original source. | Explicit direct-overwrite support. |

Modes MUST NOT be selected implicitly from backend capabilities, permissions, or failure of another mode.
A PR includes its contribution branch; callers MUST NOT need a separate branch-creation request.
Only PR mode MAY create a fork or hosted review as part of delivery.
Patch generation MUST NOT require service API authentication or a writable remote; required source acquisition MUST retain the transport-authentication contract under Native client execution.
Filesystem overwrite MUST follow `specs/behavior/hosting/filesystem.md`.
For Git sources, branch, direct, and patch delivery MUST follow the Git hosting contract; GitHub PR delivery MUST follow its hosting contract.
Noop hosting MAY simulate these modes only under its explicit test-fixture contract.

## Publication destination selection

Branch and direct modes MUST publish to the dependency's source repository unless `--push-repository` explicitly selects another repository using the same backend.
PR mode MUST use the explicitly selected publication repository or let its hosting implementation choose a suitable writable source or fork under its contract.
An explicit PR publication repository MUST be validated as an eligible review head for the original source.
Patch and overwrite modes MUST reject publication-repository overrides.

A publication override MUST remain invocation-local and MUST NOT rewrite the dependency's source declaration or requested selector.
The recipient MUST be selected unambiguously before publication; one invocation MUST NOT publish to multiple repositories.
Failure to publish to an explicit recipient MUST NOT select a third repository.
Transport or authentication failure MUST NOT be treated as proof that a fork is needed; PR fork selection MUST use hosting permission and policy information.
Creating a hosted review MUST NOT imply permission to merge it or authorize automatic merging.

## Patch artifacts

Patch mode MUST write the selected hosting implementation's contribution format to the required `--output` file under the CLI contract.
The artifact MUST describe changes at original source paths rather than installed destination paths.
The output MUST remain user-managed data rather than a cache entry or lockfile field.
An output failure MUST fail the operation rather than report an exported contribution.

## Direct source overwrite

Selecting overwrite mode MUST explicitly authorize direct writes only to the selected source mappings.
Selection and local verification MUST follow the common publication contract above, with source containment under Source inspection and extraction.
Write permissions and representability MUST follow `specs/behavior/hosting/filesystem.md`; authorization MUST NOT bypass those constraints.

## Failures and completed effects

Acquisition and source-inspection failures MUST identify the backend, repository, and affected selector, revision, or source path when applicable.
Diagnostics MUST distinguish:

- unsupported capabilities.
- missing source content.
- unavailable revisions.
- permission failures.
- client or filesystem failures.

Native-client and hosting failures MUST retain useful diagnostic information through the shared CLI error contract.

Preparation failures MUST preserve project state under Preparation and consistency in `specs/behavior/locking.md` and MUST NOT publish a partial prepared contribution.
If an external write fails after effects occur, diagnostics MUST identify known completed effects and uncertainty rather than imply rollback or successful publication.
An uncertain publication outcome MUST NOT authorize bypassing branch-collision or direct-mode protections, or silently repeating destructive writes.
If a fork or branch was created before a later failure, diagnostics MUST identify its repository and branch or revision when known.
If review creation fails or its outcome is uncertain, diagnostics MUST retain the published branch and enough target information to retry safely.
The operation MUST NOT automatically delete completed remote effects to simulate rollback.
PR delivery of changed content MUST require a confirmed matching review URL; branch publication alone MUST NOT complete that delivery.
Non-PR modes MUST remain independent of whether a hosted review could be created or merged.
