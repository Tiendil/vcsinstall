# Noop test hosting

## Goal of the document

This document describes controlled repository acquisition and refresh for tests.

## Scope

This specification covers fixture-provided repository views, acquisition observations, and injected failures.
Production transport, source inspection, and local synchronization are out of scope.

## Availability and composition

Noop hosting MUST be supplied explicitly by tests and MUST NOT be available through production CLI selection.
It MUST implement the acquisition boundary independently of the selected source adapter.
Tests MAY combine controlled views with noop, Git, or filesystem source adapters under their respective source contracts.
Acquisition MUST expose the fixture-provided view and preserve requests for a required revision or metadata refresh.
Available immutable revisions MAY be reused without simulated refresh; unavailable requested state MUST fail explicitly.
The acquired view MUST remain available through all source consumers and be released on success and failure.
Simulated refresh MUST change only explicitly controlled fixture state.

## Failures and determinism

Fixtures MUST support deterministic successful acquisition and explicit acquisition, availability, or refresh failures as needed by the exercised behavior.
They MUST NOT invoke real transport, hosted-service APIs, or native clients for acquisition.
Real source inspection MAY use its native client against fixture-prepared local data.
Injected expected failures MUST cross the ordinary result boundary without hiding internal exceptions.
Tests MUST NOT use noop results as a substitute for native Git acquisition coverage.
