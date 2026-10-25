# Repository cache

## Goal of the document

This document describes vcsinstall's shared repository cache, its location and identity rules, and its reuse and cleanup guarantees.

## Scope

This specification covers observable cache behavior during source operations and explicit cleanup, including access coordination through one cache lock file.
The following are out of scope:

- command syntax and output representation.
- configuration and lockfile schemas.
- version selection, content hashing, and installation-baseline transitions.
- VCS protocols and hosting-provider APIs.
- internal checkout layout, locking libraries, and platform-specific synchronization mechanisms.

## Cache-root selection

An explicit global `--cache-dir DIRECTORY` MUST select the complete application cache root for the invocation.
vcsinstall MUST NOT append another `vcsinstall` component to that directory.
The override MUST NOT change project configuration or the lockfile.
Home markers MUST be expanded, and relative directories MUST resolve against the invocation's working directory rather than the project root.
The directory MUST be interpreted as a filesystem path without project-path notation.

Without an override, the cache root MUST be the platform's per-user cache location followed by `vcsinstall`.
When the platform provides no usable default cache location, the fallback MUST be `<user-home>/.cache/vcsinstall`.
If neither a default nor a home-based fallback can be resolved, operations requiring a cache MUST fail with guidance to provide `--cache-dir`.
A directory's absence MUST NOT prevent its selection as a default or explicit cache root.

**Note:** Python's standard library has no unified API for discovering application cache directories. The third-party [`platformdirs`](https://platformdirs.readthedocs.io/en/stable/) library supports this discovery on Linux, macOS, and Windows. To match the layout above, its options `appauthor=False` and `opinion=False` suppress the extra author directory and `Cache` suffix on Windows; `ensure_exists=False` leaves directory creation to the operation that needs the cache.

After selecting the location and expanding home markers, absolute resolution MUST follow the Project-root resolution contract in `specs/behavior/paths.md` in `llm-tool-cli`, with the cache location as the supplied filesystem root.
The effective root MUST remain the same resolved absolute location throughout an invocation, including cleanup and its result.
The cache MAY reside outside the target project.

Cache-location resolution and filesystem validation MUST be deferred until an operation needs the cache.
An existing cache root MUST be a directory.
Missing directories MUST be created only when an operation needs to store cache data; selecting a root MUST NOT itself create it.
An unusable explicitly selected path MUST fail without falling back to the default.
Access or creation failure at a selected default root MUST also fail rather than silently switch to another location.

**Example:** If the invocation's working directory is `/work/project/tools`, `--cache-dir ../shared-cache` selects `/work/project/shared-cache`, regardless of the selected configuration file's location.

## Cache-independent operations

The following interfaces MUST remain usable without resolving or accessing a cache root, including when an explicit `--cache-dir` is unusable:

- root and command help, including bare `dependency` and `cache`.
- shell completion.
- `init`.
- `list`.
- `remove`.
- `dependency list`.
- `dependency remove`.
- `skill`.
- `version`.

Loading or validating configuration and lock state MUST NOT require repository-cache access.
Whether a source operation needs cached data MUST follow its operation contract in `specs/behavior/locking.md`; accepting `--cache-dir` MUST NOT itself make an operation cache-dependent.

## Repository identity and layout

Cached repositories MUST reside under `<cache-root>/repos/<vcs-name>/<repo-name>`.
The VCS component MUST identify the source backend, such as `git`, rather than its transport scheme, such as `git+https`.
Repository identity MUST consist of the backend and normalized native repository locator under Repository locators and identity in `specs/behavior/cli.md`, with configured filesystem paths interpreted under `specs/behavior/config.md`.
The repository-name component MUST be a single filesystem component derived deterministically from that complete identity and MUST distinguish repositories sharing a basename.
It MAY combine a readable name with a digest of the normalized locator.
Repository-entry locations MUST satisfy the Filesystem containment contract in `specs/behavior/paths.md` in `llm-tool-cli`, using the effective cache root as the supplied root.

Repository cache identity MUST be independent of:

- dependency names.
- version selectors and locked revisions.
- source paths.
- local destinations.
- the target project and its configuration location.

Dependencies using the same normalized repository MUST share its repository cache entry, including across projects and when they select different branches, tags, or revisions.
Sharing cached repository data MUST preserve independently locked dependencies under State and shared revisions in `specs/behavior/locking.md`.
Equivalent qualified and explicitly selected native locators MUST use the same cache identity.
Different native transports MUST remain separate identities; HTTPS and SSH locators MUST NOT be merged merely because they appear to address the same repository.
Backend namespaces MUST remain separate, including an ordinary HTTP backend and a Git backend using the same HTTPS address if HTTP source support is introduced.

**Examples:**

- `git+https://example.com/shared.git#branch=main` and `git+https://example.com/shared.git#tag=v1.0` select one repository cache entry. Selecting the native locator `https://example.com/shared.git` with `--vcs git` selects that same entry.
- `git+https://example.com/team-a/shared.git` and `git+https://example.com/team-b/shared.git` select different entries despite their matching basenames.
- An SSH locator for either repository selects a different entry from its HTTPS locator.

## Repository reuse and revision isolation

Repository retrieval and source preparation MUST follow Backend support and native semantics in `specs/behavior/cli.md`, using installed VCS clients for supported VCS backends.
Operations preparing source content MUST reuse valid cached repository data while obtaining the source freshness required by the command.
Operations requiring source data MUST recreate missing cache data as needed and retrieve required revisions under the applicable operation contract in `specs/behavior/locking.md`.
Inability to retrieve a required immutable revision MUST cause an operational failure.

Dependencies sharing a cached repository MUST each be able to retrieve source content at their own locked revision, even when those revisions differ.
Any cached checkouts or snapshots for specific versions MUST remain distinguishable by their immutable revisions.
Preparing one revision MUST NOT cause another dependency or operation to read content from the wrong revision, including when both select different versions of the same repository.
Changing installed content MUST NOT change cached source content or another installation through shared filesystem storage.

Source reuse and refresh MUST follow Source integrity and cache independence and Source freshness in `specs/behavior/locking.md`.
Cache availability MUST NOT change version selection or the content accepted for an existing installation baseline.
Missing cache data MUST NOT require removing or changing valid lock records.
Preparation MAY populate or refresh the cache even when the project operation subsequently fails.
Cache preparation alone MUST NOT establish project-operation success or advance project state.

**Example:** Dependencies `guides-current` and `guides-stable` use one Git repository, tracking `main` and `v1.0` respectively.
Updating `guides-current` reuses the shared cache while leaving the stable dependency's revision and installed files unchanged.
After `cache clean`, `install --locked` retrieves the required locked commits again when source data is needed; it fails if a required commit is unavailable rather than selecting a newer commit.

## Cache ownership

Cache ownership and repository identity MUST be established before existing data is reused or replaced as cached repository data.
A directory MUST NOT become disposable merely because it resides under the selected cache root or resembles a repository cache entry.
A conflict with unrelated content at a required cache location MUST fail without overwriting or deleting that content.
Data belonging to one repository identity MUST NOT satisfy a request for another.

Cache creation or refresh failure MAY leave disposable partial data.
Later operations MUST NOT treat incomplete data as a complete valid repository or source revision.
Cache failures and repairs MUST preserve project state under Preparation and consistency in `specs/behavior/locking.md`.

## Cache access locking

All vcsinstall invocations using the same effective cache root MUST coordinate through a single cache lock file at `<cache-root>/cache.lock`.
The lock MUST grant exclusive access to the entire cache, including all repositories and revisions across projects.
Different effective cache roots MUST have independent locks.
Cache-independent operations MUST NOT acquire or create this lock file.

An operation MUST acquire the lock before reading or changing cached repository data or deleting disposable cache data.
Exclusive access MUST remain effective for as long as the command or any VCS subprocess it starts accesses that data.
Sharing the cache MUST preserve repository identity and revision isolation and MUST NOT expose incomplete preparation as valid source data.

When another invocation holds the lock, a command MUST wait for up to 10 seconds to acquire it and proceed if it becomes available within that period.
If the lock remains unavailable when that timeout expires, the command MUST fail with an operational diagnostic identifying the cache root and the acquisition timeout.
Waiting MUST NOT modify:

- cached repository data.
- project configuration.
- project lockfiles.
- installed content.

Timeout MUST leave those resources unchanged by the waiting invocation.
Other lock-access failures MUST fail operationally and retain the applicable filesystem diagnostic information.

Once all protected cache access has ended, normal completion, failure, or process termination MUST release exclusive access without requiring users to remove stale coordination files.
The presence of the cache lock file alone MUST NOT indicate that the cache is in use.
Once created, that file and its containing cache root MUST remain in place across lock release and cleanup so later invocations coordinate through the same file.

**Note:** An operating-system file lock, available through libraries such as [`filelock`](https://py-filelock.readthedocs.io/en/stable/), can provide exclusive access and release after process termination. Recording a PID alone does not provide these guarantees.

## Cleanup

`cache clean` MUST remove all vcsinstall-owned disposable data from the effective cache root, including all cached repositories and their derived source data across VCS types and cached versions.
Cleanup MUST NOT be restricted to repositories referenced by the current project.
Cleanup MUST operate locally without network access or a VCS client and MUST NOT load or create project files.

Cleanup MUST preserve:

- installed content.
- project configuration.
- lockfiles.
- original source repositories.
- previously published remote contributions.
- unrelated content under the selected cache root.

Cleanup MUST NOT follow symlinks within cached data to delete content outside the effective cache root.
Cleanup MUST acquire and hold the same exclusive lock under Cache access locking, including its wait and timeout requirements, before deleting cached data.
While cleanup holds the lock, another invocation MUST NOT begin using data being deleted.

If the cache root is absent, cleanup MUST succeed as unchanged without creating a cache root or lock file.
For an existing root, cleanup MUST follow Cache access locking before inspecting disposable data; finding none MUST succeed as unchanged.
Cleanup MUST preserve the cache root and cache lock file when they exist; other coordination metadata MAY remain, but disposable cached data MUST be removed on success.
Cache-access or deletion failures MUST fail explicitly rather than report complete cleanup or fall back to another cache root.
If deletion fails after removing some data, diagnostics MUST identify partial cleanup rather than imply rollback.

Removing cached data MUST preserve locked revisions, installation baselines, and local-change comparisons under Source integrity and cache independence in `specs/behavior/locking.md`.
Later operations requiring source data MUST follow Repository reuse and revision isolation in this specification.
Command arguments and cleanup result records MUST follow `specs/behavior/cli.md`.

## Failures

Cache failures MUST identify the effective root when it can be resolved and the affected repository or filesystem path when applicable.
Diagnostics MUST distinguish:

- an unavailable location.
- unsafe or conflicting cached data.
- a cache-lock acquisition timeout.
- a failed source operation.

Filesystem diagnostics adopted from `llm-tool-cli` MUST retain their path and failure information.
Diagnostic rendering and exit statuses MUST follow `specs/behavior/cli.md`.
