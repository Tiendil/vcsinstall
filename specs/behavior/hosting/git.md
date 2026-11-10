# Git hosting

## Goal of the document

This document describes native Git repository acquisition and refresh.

## Scope

This specification covers transport access, repository-cache population, and reference observations.
Source extraction, local synchronization, and project installation state are out of scope.

## Repository access

Git acquisition MUST use the installed native Git client and its configured transport and authentication facilities.
GitHub-hosted repositories MUST use the same native acquisition contract without a GitHub client or API dependency.
The common hosting contract MUST follow `specs/behavior/vcs-interaction.md`.
Repository-cache identity, storage, exclusive access, and cleanup MUST follow `specs/behavior/cache.md`.
Native source object semantics MUST follow `specs/behavior/vcs/git.md`.

## Acquisition and refresh

Acquisition MUST expose a repository view usable for the dependency's requested selectors and required immutable revisions.
New cache entries MUST acquire the declared repository without sharing mutable source storage through hard links.
Reused cache entries MUST retain and validate the declared source identity.
Required reference refreshes MUST fetch current source observations, including moved tags, branch rewinds, deletions, and the advertised default branch.
Cached HEAD MUST NOT substitute for the source's current advertised default branch.
Source observations MUST preserve the effective-selector rules in `specs/behavior/locking.md`.

An available required immutable revision MAY be reused without contacting the original source when refresh is not requested.
Missing required objects MUST be retrieved from the declared source or reported as unavailable.
Acquisition MUST NOT advance dependency locks or reconstruct source objects from installed files.
It MUST preserve the original source's working tree, index, branches, and configuration.
Acquired cache access MUST remain protected until all source consumers finish, including failure paths.
Native command failures MUST preserve their original diagnostic information.
