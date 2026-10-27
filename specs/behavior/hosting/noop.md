# Noop test hosting

## Goal of the document

This document describes deterministic simulation of repository acquisition, refresh, and delivery without real transport or hosting services.

## Scope

This specification covers controlled acquisition and delivery state and their observable results, including failures and recovery.
The following are out of scope:

- production repository access or delivery through this adapter.
- source revision resolution and extraction.
- real native-client or hosted-service integration coverage.
- concrete Python test-double interfaces or fixture storage formats.

## Availability and composition

The hosting implementation name MUST be `noop`.
Test-only availability and the common hosting contract MUST follow `specs/behavior/vcs-interaction.md`.
Architectural responsibilities MUST follow `specs/architecture/modules_layout.md`.

Fixtures MUST support composition with:

- the noop source under `specs/behavior/vcs/noop.md`, supplying controlled views and explicitly declared simulated delivery modes.
- a real Git source using test-prepared local Git data for acquisition results and simulated native or PR delivery.
- a real filesystem source using test-created directories for acquisition results and simulated overwrite delivery.

Unsupported mode/source combinations MUST fail explicitly under the normal CLI contract.
Noop hosting MUST simulate acquisition, refresh, and the whole outgoing workflow without delegating transport or publication to a real hosting implementation.
Its effects MUST NOT invoke external clients, access network services or real user credentials, or modify real source repositories.
It MUST NOT populate or require the repository cache.
Source inspection MUST retain the independently selected backend's local version and extraction semantics, including native-client use where required.
Real source inspection MUST NOT turn simulated acquisition into real clone, fetch, or remote-reference discovery.
Patch artifacts explicitly requested by a test MAY use the real output-writing boundary; ordinary project effects MUST also retain their normal contracts.

## Controlled state and requests

A fixture MUST supply the state required by its simulated workflow, including:

- source and publication repository identities and their fork relationships.
- available repository views and observed reference and default-version metadata.
- destination revisions, content trees, and branch refs.
- account identity, permissions, and fork-creation policy where applicable.
- supported modes and eligible publication locators.
- review identities, URLs, and open, closed, or merged states where applicable.
- controlled patch bytes for patch delivery.

Repository identities and revision values MUST retain the modeled source backend's semantics, including opaque revisions for noop sources.
Account and review identities MUST remain fixture values without GitHub-specific format requirements.
Unknown repositories, unavailable revisions, or ambiguous destinations MUST fail rather than acquire invented state or permissions.
Different fixture identities and independent tests MUST remain isolated.
Effects MUST remain observable across calls within a test, including retries, unless the test explicitly changes that state.
Fixture state MUST remain independent of project files and cache history.
It MAY be shared explicitly with a noop source fixture; it MUST NOT implicitly change a separately supplied source or real repository.

Acquisition and refresh requests MUST expose the requested source identity and required revision, metadata, or freshness constraints.
Delivery requests MUST expose the workflow's:

- delivery mode.
- source and publication repository identities.
- locked base revision and any observed destination revision.
- contribution or destination branch and review target where applicable.
- commit message where applicable.
- selected source-path changes.
- known completed effects and uncertainty after failure.

Ordinary selection, source verification, content hashing, and project-consistency checks MUST remain effective.
A delivery selection without locally changed content MUST retain the common no-request, no-effect behavior.

## Simulated acquisition and refresh

Acquisition MUST return a controlled view compatible with the selected source backend, bound to the requested repository identity.
Refresh MUST expose the fixture's current reference observations and availability under Repository acquisition and refresh in `specs/behavior/vcs-interaction.md`.
Fixtures MUST be able to model moved or deleted references, changed advertised defaults, unavailable old revisions, and failed refresh independently of project locks.
A failed refresh MUST NOT return stale observations as current.

For real Git source inspection, native repository data MUST be prepared by test setup; noop hosting MUST NOT clone, fetch, or modify native refs to manufacture it.
For filesystem inspection, the view MUST refer to a test-created directory.
Repository state shared with a noop source fixture MUST be supplied explicitly.
Providing a view MUST NOT authorize publication or advance project revisions or baselines.

## Simulated native delivery

Branch and PR simulations MUST prepare the selected changes at the locked base revision, preserve unselected source content, and produce an immutable fixture contribution revision.
Branch mode MUST create a new fixture branch with the ordinary collision protection.

Direct simulation MUST follow the destination-branch selection, content protection, conditional advancement, and already-present completion rules in `specs/behavior/hosting/git.md`.
It MUST expose conflicting content and concurrent branch movement as failures rather than unconditional success.
Overwrite simulation MUST follow `specs/behavior/hosting/filesystem.md`, applying changes only to fixture source state and returning its resulting complete fingerprint.

Patch simulation MUST supply the controlled artifact bytes and metadata to the ordinary output-writing boundary without publishing a branch.
Native patch-format conformance MUST remain outside this fixture contract.
Simulated delivery MUST return the ordinary mode-specific result values without advancing installation baselines or recording publication history.

## Forks, reviews, and recovery

PR destination discovery MUST respect fixture permissions and fork relationships under the common publication contract.
An eligible fixture fork MUST be reusable, and permitted creation MUST create only fixture state.
Denied creation, an ineligible destination, or an unresolved choice MUST fail explicitly.

Review lookup and creation MUST preserve the requested source, publication head, and review target.
A newly created fixture review MUST receive a stable identity and URL and retain its contribution branch and revision.
Contribution-branch reuse and review matching MUST apply the verification, collision, and review-state rules in Review creation, retry, and completion in `specs/behavior/hosting/github.md` to fixture state, independently of that provider's native-client and identity formats.

PR completion and mode-specific effects MUST follow Delivery modes and Failures and completed effects in `specs/behavior/vcs-interaction.md`.

## Failures and determinism

Fixtures MUST be able to represent:

- failed acquisition or source-reference refresh.
- unavailable repository views or required objects.
- unavailable authentication or account identity.
- denied publication permissions and forbidden fork creation.
- missing, ambiguous, or ineligible recipients.
- unavailable contribution content and unsupported changes.
- contribution-branch collisions and direct-mode conflicts.
- concurrent destination-branch movement.
- failed patch generation or source writes.
- failed review lookup or creation.
- failures before effects and after known or uncertain publication, overwrite, fork, or review effects.

Injected failures MUST cross the ordinary hosting-error boundary and retain the context needed for recovery.
A failure after a simulated effect MUST preserve it in fixture state rather than imply rollback.
Review creation whose response is lost MUST remain discoverable on retry.
The same fixture state and inputs MUST produce the same hosting decisions and effects, apart from generated identifiers permitted by the CLI contract.
Integration coverage MUST follow Adapter integration boundaries in `specs/architecture/tests.md`.

## Example

A test pairs a real Git source in temporary repositories with noop hosting.
Test setup prepares the native repository data; noop acquisition exposes that view, and the real source resolves and extracts committed content locally.
Fixture permissions make the simulated source recipient read-only and identify an eligible writable fork.

1. Noop hosting prepares and publishes a fixture contribution branch without creating any real Git commit or ref.
2. It creates a fixture review, then reports an injected failure representing a lost response.
3. A retry supplies the reported branch; noop hosting verifies the fixture contribution and returns the matching review with the same URL.

The real source remains unchanged, and the project's installation baselines remain unchanged.
Native Git publication requires separate coverage through Git hosting.
