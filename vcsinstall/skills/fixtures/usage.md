# `vcsinstall` Usage

`vcsinstall` is being developed to install files into a repository from other repositories, with lockfile and push-back support.
The current CLI provides help, version information, this usage document, configuration initialization, dependency declaration, locking, listing, freshness checks, updates and removal, entry installation and inspection, complete project installation, entry removal, and shared cache cleanup.
`dependency add` supports Git and ordinary filesystem sources, including protected replacement of existing dependency entries.
`dependency push` delivers local changes through explicit Git, GitHub, and filesystem modes while preserving installation state.

## Commands

```bash
vcsinstall --help
vcsinstall version
vcsinstall skill usage
vcsinstall init
vcsinstall dependency add ci 'git+https://example.com/team/shared.git#branch=main'
vcsinstall add ci@/specs @/specs/shared
vcsinstall install
vcsinstall install --locked
vcsinstall list
vcsinstall dependency list
vcsinstall dependency outdated
vcsinstall dependency update
vcsinstall dependency push ci --mode patch -o ./ci-fix.patch
vcsinstall remove @/specs/shared
vcsinstall dependency remove ci
vcsinstall cache clean
```

`python -m vcsinstall` exposes the same CLI as the `vcsinstall` executable.
The `skill` command defaults to the `usage` document.
Help, version, skill, and cache cleanup commands work without project configuration.
Init creates configuration; dependency add, dependency list, dependency outdated, dependency update, dependency push, dependency remove, add, install, list, and remove require existing valid configuration.
Dependency add resolves the selected revision, writes its declaration and lock, and creates no entry mappings.

## Adding dependencies

```bash
vcsinstall dependency add ci 'git+https://example.com/team/shared.git#tag=v1.0'
vcsinstall dependency add --vcs git --branch main shared https://example.com/team/shared.git
vcsinstall dependency add local file:///home/user/shared
vcsinstall dependency add --redefine ci 'git+https://example.com/team/shared.git#tag=v2.0'
```

An explicit `--vcs` selects the backend directly and leaves locator interpretation to that backend. Otherwise, adapters recognize their own prefixes and native notation: use `git+https`, `git+ssh`, or `git+file` to identify Git. Scp-style addresses select Git. Plain paths are checked for a Git repository root first and otherwise select an ordinary filesystem directory. Plain `file://` selects filesystem content; use `--vcs filesystem` to select live files explicitly, including in a Git working tree. Unqualified remote URLs are checked through a bounded native Git metadata query. Operational probe failures remain errors, and ordinary HTTP content is unsupported.
Git selectors accept `#branch=NAME`, `#tag=NAME`, `#rev=COMMIT`, or an unambiguous bare `#REF`. The mutually exclusive `--branch`, `--tag`, and `--rev` options are aliases and cannot accompany a fragment. Percent-encode literal fragment delimiters. Without a selector, Git uses and records the advertised default branch. Git revision abbreviations become full commit identifiers.
Configuration and locks store selection as `selector = { kind = "branch", value = "main" }`, using kind `branch`, `tag`, or `revision`. An omitted selector keeps backend default behavior; the lock's `revision` separately records the resolved content ID.
Filesystem sources track their complete live content fingerprint, or accept a complete `--rev sha256:...` pin. They have no branches or tags and retain no historical snapshots.

An existing name requires `--redefine`, even for an identical declaration. Redefinition re-infers an omitted selector and refreshes every entry of that dependency at one revision while preserving entry mappings. Each entry owns its complete destination tree; extra local files count as local changes, and conflicting content fails before project changes. Missing selected files are restored. Changed entry roots are removed and recopied in full; entries already matching the source stay untouched. Identical redefinition reports `unchanged` and preserves project-file bytes.

Git sources require the installed Git client and use a shared per-user cache. Put `--cache-dir DIRECTORY` before `dependency` to override its root. Dependency add resolves the cache root for every backend. Filesystem sources do not create cache data. Project-independent commands do not resolve or access the cache. Native authentication remains owned by Git.
Automation success emits a `dependency` record, followed by ordered `installation` records for refreshed entries. Errors use the selected shared protocol.

## Listing dependencies

```bash
vcsinstall dependency list
vcsinstall -p automation dependency list
```

List reports every declared dependency, including those without entries, ordered by name. Each record includes the backend, normalized repository, effective selector, and matching locked revision, or null when no matching lock exists. An omitted Git selector uses a matching lock's recorded branch; without one it reports `ref_kind = default` and null `ref`.

The `entries` count includes each declared file or directory mapping once. `modified`, `missing`, and `unlocked` count entries whose existing files differ from their baseline, whose expected files are missing, or whose matching baseline is unavailable. Remaining entries are unmodified. Local additions and regular-file obstructions count as modifications; modifications take precedence over missing files. Empty directories do not affect comparison. An entry is locked when both its dependency revision lock and installation baseline match its declaration; otherwise it is unlocked. Malformed or unreadable state fails.

The command accepts no arguments or command-specific options. It reads local content without changing project files, needs no source repository, network, VCS client, or repository cache, and ignores `--cache-dir`. It does not check upstream freshness. Automation emits metadata-only `dependency` records with operation `dependency list` and the four counts; it emits no installation records. An empty project succeeds without result records or creating a lockfile.

## Listing entries

```bash
vcsinstall list
vcsinstall list @/specs/shared
vcsinstall -p automation list @/specs/shared
```

Without paths, list reports every declared entry. Explicit paths select exact declared destination roots, including missing installations; they accept `@/` identifiers, absolute contained paths, or paths relative to the invocation directory. Surrounding whitespace is stripped, equivalent paths select an entry once, and results are ordered by canonical destination. Parent paths, child files, and globs do not select a directory entry. Unknown selections fail. Use `--` before positional filesystem paths beginning with `-`.

Each installation record identifies its owning `dependency`, `destination`, backend, repository, `source_path`, effective selector, matching locked `revision`, and local `status`:

- `unmodified` — All local files agree with the baseline, including expected absence or an absent empty-directory installation.
- `modified` — Existing content differs, extra files exist within the entry, or a regular file obstructs a required directory. This takes precedence over missing files.
- `missing` — Expected files are absent and existing files otherwise agree with the baseline.
- `unlocked` — The dependency lock or entry baseline is missing or does not match the declaration. The revision is null.

Human and LLM output label the dependency on each entry. Automation emits metadata-only `installation` records with operation `list`, without mutation outcomes or dependency summaries. Dependencies without selected entries produce no results. An empty project succeeds without records. Local changes, missing content, and unlocked entries still exit successfully; malformed state and unreadable or unsupported selected content fail.

List preserves configuration, lockfile bytes, and installed content. It needs no source repository, network, VCS client, or repository cache, ignores `--cache-dir`, and does not check source freshness. Directory comparison includes all local files, including hidden or ignored additions.

## Checking source freshness

```bash
vcsinstall dependency outdated
vcsinstall dependency outdated ci formatting
vcsinstall -p automation dependency outdated ci
```

Without names, outdated checks every declared dependency, including those without entries. Names select exactly those dependencies; repeated names select each once. Unknown names fail as invalid arguments. There are no command-specific options.

Every selected dependency must have a matching revision lock before any source fetching. Use `install` to establish missing locks or `dependency update NAME` to refresh a dependency and its entries. Unselected locks and installation baselines need not match. Installed files may be missing or modified without blocking this check.

The command refreshes and resolves the effective branch, tag, or default selector, then compares its complete immutable revision with the lock. Moved tags, rewound branches, and new commits with identical content count as outdated. An omitted Git selector retains a matching lock's recorded branch even when the remote default changes. Revision pins keep selecting their pinned state and do not refresh merely to find newer commits. Required acquisition or resolution failures remain errors; stale cached references do not satisfy a failed refresh. Filesystem sources compare the complete live fingerprint, including changes outside installed entry paths; an unavailable pinned fingerprint fails.

Success emits only metadata-only `dependency` records, ordered by name, with operation `dependency outdated`. Each includes source identity, effective selector, locked `revision`, and differing `current_revision`. It emits no entry counts or installation records and makes no promise that an update can succeed. Finding outdated dependencies exits successfully; an empty selection or no differences produces no result records.

Configuration, lockfile bytes, and installed content remain unchanged. Put global `--cache-dir DIRECTORY` before `dependency` to select the cache root. The command resolves that root for every backend and may populate or refresh Git cache data; filesystem sources do not create cache data. Git source access requires the native Git client and its configured authentication.

## Updating dependencies

```bash
vcsinstall dependency update
vcsinstall dependency update ci formatting
vcsinstall -p automation dependency update ci
```

Without names, update selects every dependency, including those without entries. Explicit names select exactly those dependencies, with duplicates selecting each once. Unknown names fail before source access. There are no command-specific options or entry-path selectors, and there is no root `update` command.

Each selected dependency resolves its configured selector once and refreshes all its entries at that revision. Branches and tags retain their literal names; Git declarations without a selector retain a matching lock's recorded branch even when the remote default changes. Revision pins remain pinned. Filesystem live sources use their current complete fingerprint without requiring the old source state to remain available. Configuration and unselected installations remain unchanged.

Every declared entry needs a trustworthy prior baseline. Existing local files must collectively match either that baseline or the selected source across the complete entry tree; independent edits are not merged. Missing selected files are restored, and upstream additions, deletions, and file/directory changes are applied. Changed entry roots are removed and recopied completely; entries already equal to the selected source receive no filesystem writes. Source deletions record expected absence while retaining the mapping for later recreation.

All selected entries are prepared before any project mutation. A preparation failure leaves every selected file and lock unchanged. A failure while applying files can leave content inconsistent with the prior lock and is reported explicitly. Successful updates write locks and baselines together after file application; unchanged state preserves lockfile bytes.

Success emits metadata-only `dependency` records ordered by name, followed by `installation` records ordered by destination. Records use operation `dependency update`, outcome `updated` or `unchanged`, and include `previous_revision` and selected `revision`; installation status is `unmodified`. An empty project succeeds without result records or creating a lockfile. Put global `--cache-dir DIRECTORY` before `dependency` to select the Git cache; filesystem sources do not create cache data.

## Removing dependencies

```bash
vcsinstall dependency remove ci
```

Dependency remove requires exactly one declared name and refuses dependencies with declared entries, identifying their destinations. Remove or reassign those entries first; there is no cascade or force option. Unknown names fail as invalid arguments.

It removes the declaration, shared revision lock, and obsolete installation baselines, preserving installed files and unrelated records. A baseline still protecting an entry reassigned to another dependency must be reconciled first. Missing lock state does not block removal or create a lockfile; existing metadata must still validate.

The command needs no source repository, network, Git client, or repository cache and ignores `--cache-dir`. Automation success emits one metadata-only `dependency` record with operation `dependency remove` and outcome `removed`. It describes the definition observed before removal, with its matching locked revision or null when none matches. An omitted Git selector reports a matching lock's inferred branch, or `ref_kind = default` and null `ref` without one.

## Adding entries

```bash
vcsinstall add ci@/specs @/specs/shared
vcsinstall add ci@/LICENSE @/licenses/shared.txt
vcsinstall add --redefine ci@/guides @/docs/shared
vcsinstall add --allow-outdated ci@/examples @/examples/shared
```

Add declares and installs exactly one file or complete directory from an existing dependency's matching lock. It keeps that shared revision and all other entries unchanged. Use `install` to establish a missing or mismatching dependency lock, or `dependency update NAME` to refresh it before adding entries.

Sources use `NAME@/PATH`, with `NAME@/` selecting the repository root. Source paths retain literal percent signs, plus signs, query characters, and fragment characters; quote them for your shell when needed. Repository URLs and version options belong to `dependency add`.
The destination is the exact final file or directory path. It accepts `@/` project identifiers, contained absolute paths, or paths relative to the invocation directory. An existing directory does not append the source basename. Destinations cannot overlap another entry or either project file.

An already declared destination requires `--redefine`, even for an identical mapping. Redefinition may change the owning dependency but still verifies the previous baseline and protects local edits and additions. Identical redefinitions report `unchanged`; missing selected files are restored. Files already equal to selected source content receive no writes. Empty directory selections record an empty manifest without requiring a destination directory.

Add refreshes and checks the dependency's effective selector against its locked revision. A moved branch, tag, or live filesystem fingerprint fails unless `--allow-outdated` is supplied; `--redefine` grants no freshness permission. Even with `--allow-outdated`, refresh and resolution must succeed and the locked source path must remain available. Filesystem sources retain no historical snapshots, so a changed live source cannot reproduce an old fingerprint.
Put global `--cache-dir DIRECTORY` before `add` to override the shared Git cache. Both add commands resolve its root for every backend; filesystem sources do not create cache data. Automation success emits one metadata-only `installation` record with operation `add`, status `unmodified`, and outcome `installed` or `unchanged`.

## Installing the project

```bash
vcsinstall install
vcsinstall install --locked
vcsinstall -p automation install
```

Install covers every declared dependency and entry, including dependencies without entries. It accepts no positional arguments or source-selection options. Configuration remains unchanged. Matching dependency locks retain their revisions even when branches or tags move; newly declared entries also use those revisions. Missing or changed bindings are resolved once per dependency, and an omitted Git selector retains a matching lock's recorded branch.

Existing local files must collectively agree with their prior baseline or the selected source across the complete entry tree. Local additions are protected, missing files are restored, and changed entry roots are recopied completely. Entries already matching a trustworthy baseline need no source, VCS client, or cache access. Every dependency is prepared before any project mutation. Preparation failure preserves all installed files and records; an application failure may leave content inconsistent with the prior records and is reported explicitly.

Ordinary install establishes complete matching lock state and removes obsolete records after successful preparation and installation. It leaves content from manually removed declarations in place. An empty project succeeds without result records and establishes an empty lockfile.

`--locked` requires an existing lockfile with exactly the declared dependencies and entry mappings before fetching source content. Missing, changed, or obsolete records fail. Lockfile bytes remain unchanged. Missing source data may be fetched at its locked immutable revision without refreshing tracking references; unavailable revisions fail rather than advance. This mode does not imply offline execution. Filesystem sources retain no historical snapshots, so restoration requires the complete live source fingerprint to match the lock, including files outside the selected entry paths.

Put global `--cache-dir DIRECTORY` before `install` to override the cache root. Install resolves that root for every backend but creates cache data only when needed. Automation success emits metadata-only `dependency` records ordered by name, followed by `installation` records ordered by destination. Records use operation `install` and outcome `installed` or `unchanged`; installation status is `unmodified`.

## Publishing local changes

```bash
vcsinstall dependency push ci --mode branch -m "Clarify the shared guide"
vcsinstall dependency push ci @/specs/shared --mode pr --branch contribute/shared-guide
vcsinstall dependency push ci --mode direct --branch release/1.x
vcsinstall dependency push ci --mode patch --output ./ci-fix.patch
vcsinstall dependency push local-guides --mode overwrite
```

Push requires one declared dependency and an explicit `--mode`: `pr`, `branch`, `direct`, `patch`, or `overwrite`. There is no root `push` command. Without paths it selects only that dependency's changed entries. Explicit paths use exact declared entry roots and may include unchanged entries, but cannot select entries of another dependency. A directory contributes its complete local tree, including new, hidden, generated, and ignored files and deletions. Every inspected entry needs a matching dependency lock and baseline. Unselected entries need no local verification. An unchanged selection needs no source or hosting access.

Branch, PR, and patch modes prepare against the locked commit. Branch mode creates one new branch, automatically named unless `--branch` is supplied; an existing branch causes failure. Direct mode targets one existing branch, selected by `--branch` or the effective branch selector. It uses the current recipient commit, preserving unrelated upstream changes, and permits each complete selected entry only when the recipient matches its baseline or already matches the proposed local content. Different remote changes anywhere in a selected directory cause a conflict. Already-present contributions succeed without another commit. A tag or revision pin requires an explicit direct `--branch` or PR `--base`.

PR mode uses GitHub hosting, inferred from github.com or an unambiguous configured host, or selected with `--hosting-provider github`. The installed `gh` client supplies the account and authentication. The source repository and selected base are the review target; the locked commit must be its ancestor. The adapter publishes to a writable source or an eligible account-owned fork, creating a fork when permitted. `--push-repository` overrides the recipient for PR, branch, or direct modes and never changes the installation source. PR recipients must belong to the source's fork network. The option cannot contain a source-version fragment. Relative repository paths resolve against the invocation directory.

PR completion requires a confirmed matching review URL. Retry with the reported branch and the same selection, message, and base. An existing branch can be reused only when its complete tree, parent, and message match. A matching open PR is recovered; conflicting, closed, or merged reviews fail with guidance. Failures retain known fork and branch effects and uncertainty for safe recovery. Native Git modes remain usable without gh or API authentication, including with explicit GitHub hosting.

Patch mode requires `--output PATH` / `-o PATH` naming the complete artifact file. `-` and project-path notation are rejected. Home markers expand and relative paths use the invocation directory. The target must be absent or a regular unmanaged file outside project records, all entry destinations, cache data, and source administration. Output is streamed directly after contribution preparation; a failed export can leave an empty or partial file, including truncating an existing artifact. The mailbox patch includes author, message, binary changes, full blob identifiers, and locked-base information and supports `git am`. An unchanged selection leaves the artifact untouched.

Filesystem sources support only explicit overwrite. It replaces complete selected source mappings despite changes since installation, preserves unrelated source content, and reports the resulting complete fingerprint. It requires no Git, gh, or cache access. Unsupported objects, parent obstructions, or changes that cannot preserve unrelated content fail before writes. Partial-write failures are reported without implying rollback.

All modes except overwrite accept repeated `--message` / `-m` paragraphs. A nonempty default message avoids editors. `--base` applies only to PR; `--branch` to PR, branch, and direct; `--output` only to patch; `--message` does not apply to overwrite. `--hosting-provider` overrides automatic selection for acquisition and delivery. Every operation uses the same defaults: GitHub for unambiguously recognized GitHub sources, native Git for other Git sources, and filesystem for directories. The selected provider validates the requested mode. Optional host recognition uses local metadata and client configuration without service API access; unavailable or ambiguous recognition preserves native Git access.

Push leaves configuration, lockfile bytes, installed files, and recorded baselines unchanged. Local additions remain `modified`; local deletions remain `missing`. A later successful dependency update records matching source content as the new baseline. Results are ordered installation records with operation `dependency push`, mode, unchanged local status, and outcome `pushed`, `exported`, or `unchanged`. Effect fields report the actual recipient, branch, contribution revision, hosting provider, PR base and URL, or absolute patch file. Unchanged entries have null effect fields. The `revision` field always remains the installation revision. A default selection without changes emits no records.

## Removing entries

```bash
vcsinstall remove @/specs/shared @/licenses/shared.txt
vcsinstall remove --keep-files @/docs/shared
vcsinstall remove --force @/examples/shared
```

Remove requires one or more exact declared entry roots, using `@/` identifiers, contained absolute paths, or paths relative to the invocation directory. Repeated paths select an entry once. Child paths and globs do not select their owning entry. Missing installed content does not prevent removal of its declaration and baseline.

Ordinary removal deletes each selected destination root and its complete contents after verifying every remaining file against a matching installation baseline. Modified files, local additions, or unavailable matching baselines cause failure before any selected files or metadata change. `--force` authorizes deletion of modified or unverified content within the selected roots; containment and existing metadata must still validate.

`--keep-files` removes only the selected declarations and baselines, leaving their content untouched as unmanaged project files. It requires no content verification or matching baseline and takes precedence over `--force`. Neither mode creates a missing lockfile. Dependency declarations and shared revision locks remain after their last entry is removed; unselected entries retain their content and records.

Remove uses no source repository, network, Git client, or repository cache, and ignores `--cache-dir`. Automation success emits one metadata-only `removal` record per selected entry, ordered by canonical destination, with operation `remove`, dependency name, destination, outcome `removed`, and Boolean `keep_files`.

## Cleaning the cache

```bash
vcsinstall cache clean
vcsinstall --cache-dir ./temporary-cache cache clean
```

Cleanup removes owned repository entries and their cached revisions from the selected shared cache across projects and backends. It preserves installed content, configuration, lockfiles, original sources, remote contributions, and unrelated files under the cache root. Symlink targets outside cached data remain untouched. It needs no project, network, or VCS client and ignores unusable `--config` values.

Put global `--cache-dir DIRECTORY` before `cache` to select the complete cache root; home markers expand and relative paths resolve against the invocation directory. Otherwise, the platform's per-user vcsinstall cache is used. Cleanup takes the same exclusive cache lock as repository access, waits up to 10 seconds, and fails if the cache remains busy. It keeps an existing cache root and its lock file. An absent cache succeeds without creating either.

No selectors, command-specific options, or confirmation are needed. Bare `cache` shows help without accessing the cache. Automation success emits one metadata-only `cache` record with operation `clean`, absolute `cache_dir`, and outcome `cleaned` or `unchanged`. Access or deletion failures fail explicitly; deletion failures identify possible partial cleanup. Later installations fetch missing cached data at their required revisions.

## Protocols

Put global options before the command:

```bash
vcsinstall -p human version
vcsinstall -p llm skill usage
vcsinstall -p automation version
```

The human protocol is intended for terminal users.
The LLM protocol frames output cells for coding agents.
The automation protocol emits JSON Lines for programs.
Version output defaults to the human protocol; skill output defaults to the LLM protocol.

## Project files

The default configuration filename is `vcsinstall.toml`. Loading searches the current directory and its parents for the nearest configuration.
Use the global `--config PATH` option to select a different file, with relative paths based on the invocation's working directory:

```bash
vcsinstall --config project/custom.toml init
vcsinstall --config project/custom.toml dependency add ci git+https://example.com/team/shared.git
```

Init uses the current directory when no path is supplied, without searching parents. It writes a version 1 starter with commented examples, requires an existing parent directory, refuses to overwrite existing files, and creates no lockfile.

The selected configuration's parent is the project root. Its lockfile is always the sibling `vcsinstall.lock`; it has no separate discovery or path option.
Dependency add validates the entire configuration and any existing lockfile. A missing lockfile is allowed; malformed, unreadable, or symlinked project files fail.
Loading preserves stale lock records and does not change either file, fetch repositories, or inspect installed contents.
Help, version, skill, and cache cleanup commands ignore unusable `--config` values.
