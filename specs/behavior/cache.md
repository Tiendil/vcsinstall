# Repository cache

## Goal of the document

This document describes vcsinstall's repository-cache policy and its application of shared storage contracts.

## Scope

This specification covers repository-cache behavior observed across vcsinstall operations and projects.
Generic cache storage mechanics, command syntax, and project record formats are out of scope.

## Applicability

Repository-cache use MUST follow the source and hosting contracts identified in `specs/behavior/vcs-interaction.md`.
The repository storage and reuse requirements below MUST apply to acquired repository views and all source or hosting operations using them.
Operations using only uncached source and hosting capabilities MUST NOT acquire the cache lock, create its root, or populate cached repository data.
Explicit `cache clean` MUST retain its cache operation contract independently of the current project's source backends.
Hosting selection and publication to another repository MUST NOT change the original source's cache identity.
If another repository is itself cached, its storage MUST follow its own backend and normalized locator identity under Repository identity below.

## Cache-root selection

Cache-root selection MUST follow Cache roots under Tool storage locations in `specs/behavior/paths.md` in `llm-tool-cli`, using the canonical tool name `vcsinstall`, global `--cache-dir DIRECTORY` as the optional override, and the invocation's working directory for relative paths.
An explicit override MUST select the complete application cache root for the invocation.
The override MUST NOT change project configuration or the lockfile.
The directory MUST be interpreted as a filesystem path without project-path notation.

Unavailable platform discovery MUST use the home-based fallback permitted by that contract.
If neither a default nor a home-based fallback can be resolved, command setup MUST fail with unchanged shared diagnostics and corrective guidance.

The effective root MUST remain the same resolved absolute location throughout an invocation, including cleanup and its result.
The cache MAY reside outside the target project.

Every executing command MUST have its effective cache location selected under Invocation context in `specs/behavior/cli.md` in `llm-tool-cli`, including commands that do not use cached data.
Filesystem checks needed to read or write cached data MUST remain deferred until those capabilities are used.

**Example:** If the invocation's working directory is `/work/project/tools`, `--cache-dir ../shared-cache` selects `/work/project/shared-cache`, regardless of the selected configuration file's location.

## Cache-independent operations

Help, completion, and bare command groups MUST remain usable without resolving a cache root, including when an explicit `--cache-dir` is unusable.
The following commands MUST NOT create cache storage, acquire cache locks, or read cached data:

- `version`.
- `skill`.
- `init`.
- `list`.
- `remove`.
- `dependency list`.
- `dependency remove`.

Loading or validating configuration and lock state MUST NOT require repository-cache access.
Project coordination MUST remain independent of this cache under Project operation locking in `specs/behavior/cli.md`.
Whether acquisition, source inspection, or delivery needs cached data MUST follow the applicable operation and adapter contracts; selecting a cache location MUST NOT itself require cache storage to exist or be accessible.

## Repository identity

Repository identity MUST follow Repository locators and identity in `specs/behavior/vcs-interaction.md`, with configured filesystem paths interpreted under `specs/behavior/config.md`.
Cached data MUST be associated with that complete identity and MUST distinguish repositories sharing a basename.
Repository-entry acquisition MUST follow Cache entries in `specs/behavior/cache.md` in `llm-tool-cli`, using the effective cache root and that repository identity.

Repository cache identity MUST be independent of:

- dependency names.
- version selectors and locked revisions.
- source paths.
- local destinations.
- the target project and its configuration location.
- the hosting implementation or selected delivery mode.

Dependencies using the same normalized repository MUST share its repository cache entry, including across projects and when they select different branches, tags, or revisions.
Sharing cached repository data MUST preserve independently locked dependencies under State and shared revisions in `specs/behavior/locking.md`.

**Examples:**

- `git+https://example.com/shared.git#branch=main` and `git+https://example.com/shared.git#tag=v1.0` select one repository cache entry. Selecting the native locator `https://example.com/shared.git` with `--vcs git` selects that same entry.
- `git+https://example.com/team-a/shared.git` and `git+https://example.com/team-b/shared.git` select different entries despite their matching basenames.
- An SSH locator for either repository selects a different entry from its HTTPS locator.

## Repository reuse and revision isolation

Repository acquisition and refresh MUST follow Repository acquisition and refresh in `specs/behavior/vcs-interaction.md`.
Inspection of available cached views MUST follow Source inspection and extraction in that specification.
Operations preparing source content MUST reuse valid cached repository data while obtaining the source freshness required by the command.
Hosting acquisition, refresh, and publication using cached objects MUST retain the same repository identity, revision isolation, and exclusive-access guarantees as local source inspection.
Native Git and GitHub hosting MUST share Git's cache namespace rather than create hosting-specific repository copies.
Prepared publication commits MUST NOT replace source content or make a recipient's state authoritative for the dependency's source selection.
Operations requiring source data MUST recreate missing cache data as needed and retrieve required revisions under the applicable operation contract in `specs/behavior/locking.md`.
Rebuilding cached views MUST use the hosting acquisition contract without requiring source inspection to perform transport access.

Dependencies sharing a cached repository MUST each be able to retrieve source content at their own locked revision, even when those revisions differ.
Any cached checkouts or snapshots for specific versions MUST remain distinguishable by their immutable revisions.
Preparing one revision MUST NOT cause another dependency or operation to read content from the wrong revision, including when both select different versions of the same repository.
Changing installed content MUST NOT change cached source content or another installation through shared filesystem storage.
Content verification MUST use the selected source manifest under Content identity and Local comparison in `specs/behavior/files-and-directories.md` rather than hash the physical repository-cache directory.

Source reuse and refresh MUST follow Source integrity and cache independence and Source freshness in `specs/behavior/locking.md`.
Cache availability, reuse, and cleanup MUST NOT change locked revisions, installation baselines, or local-content comparisons, or the content accepted for an existing baseline.
Missing cache data MUST NOT require removing or changing valid lock records.
Preparation MAY populate or refresh the cache even when the project operation subsequently fails.
Cache preparation alone MUST NOT establish project-operation success or advance project state.

After failed preparation or refresh, later operations MUST NOT treat incomplete cached data as a complete valid repository or source revision.
Cache failures and repairs MUST preserve project state under Preparation and consistency in `specs/behavior/locking.md`.

**Example:** Dependencies `guides-current` and `guides-stable` use one Git repository, tracking `main` and `v1.0` respectively.
Updating `guides-current` reuses the shared cache while leaving the stable dependency's revision and installed files unchanged.
After `cache clean`, `install --locked` retrieves the required locked commits again when source data is needed; it fails if a required commit is unavailable rather than selecting a newer commit.

## Cache access locking

Repository access and cleanup MUST follow Exclusive access in `specs/behavior/cache.md` in `llm-tool-cli`, using the effective root and a 10-second acquisition timeout.
The protected lifetime MUST cover every source or hosting operation using cached repository data, including its VCS subprocesses while vcsinstall remains running.
Cache-independent operations MUST NOT acquire cache access or create its coordination data.
Sharing the cache MUST preserve repository identity and revision isolation and MUST NOT expose incomplete preparation as valid source data.
Waiting or failed acquisition MUST NOT change project configuration, lockfiles, or installed content.

## Cleanup

`cache clean` MUST apply Cleanup in `specs/behavior/cache.md` in `llm-tool-cli` to all vcsinstall-owned disposable repository data in the effective cache, including derived source data across VCS types and cached versions.
Its acquisition policy MUST follow Cache access locking above.
Cleanup MUST NOT be restricted to repositories referenced by the current project.
Cleanup MUST NOT require a VCS client or load or create project files.

Cleanup MUST preserve:

- installed content.
- project configuration.
- lockfiles.
- project operation coordination files under Project operation locking in `specs/behavior/cli.md`.
- original source repositories.
- previously published remote contributions.

Later operations requiring source data MUST follow Repository reuse and revision isolation in this specification.
Command arguments and cleanup result records MUST follow `specs/behavior/cli.md`.

## Failures

Cache storage failures MUST follow Failures in `specs/behavior/cache.md` in `llm-tool-cli`.
Failed source operations MUST remain distinguishable from storage failures and retain their repository context under `specs/behavior/vcs-interaction.md`.
Diagnostic rendering and exit statuses MUST follow `specs/behavior/cli.md`.
