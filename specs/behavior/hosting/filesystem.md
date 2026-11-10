# Filesystem hosting

## Goal of the document

This document describes acquisition of an ordinary filesystem source directory.

## Scope

This specification covers directory access and acquisition failures.
Revision fingerprints, extraction, local synchronization, and project state are out of scope.

## Directory access

Filesystem acquisition MUST expose the declared existing readable source directory directly.
Unavailable, unreadable, or non-directory sources MUST fail explicitly.
It MUST NOT create source directories, cache repository snapshots, acquire repository-cache locks, or invoke native VCS clients.
Acquisition MUST preserve the source content and declared repository identity.
The common hosting contract MUST follow `specs/behavior/vcs-interaction.md`.
Live revision availability and source extraction MUST follow `specs/behavior/vcs/filesystem.md`.
Local synchronization MUST follow its independent operation contract in `specs/behavior/locking.md`.
