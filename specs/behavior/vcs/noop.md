# Noop test sources

## Goal of the document

This document describes a deterministic source backend for testing backend-independent behavior without native VCS clients or real publication.

## Scope

This specification covers version resolution and extraction from controlled repository views, including injected read failures at the test-source boundary.
The following are out of scope:

- production source installation through this backend.
- native VCS protocol, authentication, or real hosting-provider behavior.
- concrete Python test-double interfaces or fixture storage formats.
- native-client integration-test coverage.

## Availability and identity

The backend name MUST be `noop`.
Test-only availability and the common source contract MUST follow `specs/behavior/vcs-interaction.md`.

The native repository locator MUST be a nonempty fixture identifier whose spelling and case remain literal.
It MUST NOT implicitly access a filesystem location or network address.
Repository identity MUST consist of `noop` and that identifier, independently of dependency names and selected versions.
Different fixture identifiers MUST remain isolated, and independent tests MUST NOT inherit mutable fixture state accidentally.

Noop operations MUST NOT invoke VCS executables, access network services, or populate or require the repository cache.
The backend MUST NOT modify real source repositories.
Ordinary project effects performed by the operation being tested MUST still follow their normal contracts; selecting noop MUST NOT turn installation or lockfile handling into unconditional success.
Source and hosting responsibilities MUST follow the adapter boundaries in `specs/architecture/modules_layout.md`.

## Controlled revisions

A source fixture MUST supply an available repository view with immutable content states identified by complete nonempty revision identifiers.
Identifiers MUST be opaque fixture values; noop MUST NOT require Git-shaped hexadecimal hashes or invent source state for an unknown identifier.
One identifier MUST NOT change its content meaning during the test.

Fixtures MAY provide branch names, tag names, and revision pins.
An explicit fixture default MAY select among those supported selectors.
Their resolution MUST follow the same selector distinctions and ambiguity rules as the common contract.
Changed branch or tag observations in a supplied view MUST affect subsequent resolution without altering a revision already selected for an operation.
A missing default, reference, or requested revision MUST cause the appropriate explicit failure rather than fabricate one.

Revision availability MUST be controlled independently of the project's lock state so tests can represent an unavailable old revision.
All entries in one dependency operation MUST obtain content at its one selected fixture revision.
Fixture changes MUST NOT advance project locks or installation baselines except through the operation being tested.

## Source content

A revision MUST expose a repository-relative tree of files, directories, and absent paths.
Files MUST retain exact supplied bytes, including binary content, and directories MUST retain literal names and empty directories.
Source `.` MUST select the fixture's root directory.
An unknown path at an available revision MUST be reported as absent; an unavailable revision MUST remain a retrieval failure.
Fixture enumeration order MUST NOT affect content identity or result ordering.

Extraction MUST enforce the common source-path and representability contracts without VCS filters or ignore patterns.
Installed content MUST be ordinary content that the real hashing and local-state inspection contracts can verify.
Unsupported objects and requested read failures MUST fail rather than be converted into empty files or directories.
The test fixture mechanism MAY model such failures without prescribing a concrete filesystem representation.

## Hosting composition

Acquisition, refresh, and outgoing simulations MUST follow `specs/behavior/hosting/noop.md` and MUST be supplied independently.
Source inspection MUST interpret the supplied view without simulating acquisition, refreshing references, or inventing source access.
Source and hosting fixture repository state MAY be shared explicitly by a test; simulated delivery MUST NOT change a separately supplied source fixture implicitly.

## Failures and determinism

Fixtures MUST be able to represent:

- missing or ambiguous selectors.
- unavailable revisions and absent source paths.
- failed local inspection or extraction.
- unsupported content or operations.
- denied read permissions.

Injected failures MUST cross the same observable source-error boundary as real backend failures and MUST NOT bypass normal selection, hashing, or project-consistency checks.
The same fixture state and inputs MUST produce the same source content and resolution.
Integration coverage MUST follow Adapter integration boundaries in `specs/architecture/tests.md`.
