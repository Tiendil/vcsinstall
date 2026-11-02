# `vcsinstall` Usage

`vcsinstall` is being developed to install files into a repository from other repositories, with lockfile and push-back support.
The current CLI provides help, version information, this usage document, configuration initialization, dependency declaration, locking, listing, freshness checks and removal, entry installation, and entry removal.
`dependency add` supports Git and ordinary filesystem sources, including protected replacement of existing dependency entries.
Other dependency operations, general installation, and push-back commands are not implemented yet.

## Commands

```bash
vcsinstall --help
vcsinstall version
vcsinstall skill usage
vcsinstall init
vcsinstall dependency add ci 'git+https://example.com/team/shared.git#branch=main'
vcsinstall add ci@/specs @/specs/shared
vcsinstall dependency list
vcsinstall dependency outdated
vcsinstall remove @/specs/shared
vcsinstall dependency remove ci
```

`python -m vcsinstall` exposes the same CLI as the `vcsinstall` executable.
The `skill` command defaults to the `usage` document.
Help, version, and skill commands work without project configuration.
Init creates configuration; dependency add, dependency list, dependency outdated, dependency remove, add, and remove require existing valid configuration.
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

## Checking source freshness

```bash
vcsinstall dependency outdated
vcsinstall dependency outdated ci formatting
vcsinstall -p automation dependency outdated ci
```

Without names, outdated checks every declared dependency, including those without entries. Names select exactly those dependencies; repeated names select each once. Unknown names fail as invalid arguments. There are no command-specific options.

Every selected dependency must have a matching revision lock before any source fetching. Missing or mismatching locks require `install` or `dependency update NAME`; these commands are planned, so use `dependency add --redefine` where applicable in the current CLI. Unselected locks and installation baselines need not match. Installed files may be missing or modified without blocking this check.

The command refreshes and resolves the effective branch, tag, or default selector, then compares its complete immutable revision with the lock. Moved tags, rewound branches, and new commits with identical content count as outdated. An omitted Git selector retains a matching lock's recorded branch even when the remote default changes. Revision pins keep selecting their pinned state and do not refresh merely to find newer commits. Required acquisition or resolution failures remain errors; stale cached references do not satisfy a failed refresh. Filesystem sources compare the complete live fingerprint, including changes outside installed entry paths; an unavailable pinned fingerprint fails.

Success emits only metadata-only `dependency` records, ordered by name, with operation `dependency outdated`. Each includes source identity, effective selector, locked `revision`, and differing `current_revision`. It emits no entry counts or installation records and makes no promise that an update can succeed. Finding outdated dependencies exits successfully; an empty selection or no differences produces no result records.

Configuration, lockfile bytes, and installed content remain unchanged. Put global `--cache-dir DIRECTORY` before `dependency` to select the cache root. The command resolves that root for every backend and may populate or refresh Git cache data; filesystem sources do not create cache data. Git source access requires the native Git client and its configured authentication.

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

Add declares and installs exactly one file or complete directory from an existing dependency's matching lock. It keeps that shared revision and all other entries unchanged. A missing or mismatching dependency lock requires `install` or `dependency update NAME` before adding entries; those commands are planned, so use `dependency add --redefine` for an existing dependency where applicable in the current CLI.

Sources use `NAME@/PATH`, with `NAME@/` selecting the repository root. Source paths retain literal percent signs, plus signs, query characters, and fragment characters; quote them for your shell when needed. Repository URLs and version options belong to `dependency add`.
The destination is the exact final file or directory path. It accepts `@/` project identifiers, contained absolute paths, or paths relative to the invocation directory. An existing directory does not append the source basename. Destinations cannot overlap another entry or either project file.

An already declared destination requires `--redefine`, even for an identical mapping. Redefinition may change the owning dependency but still verifies the previous baseline and protects local edits and additions. Identical redefinitions report `unchanged`; missing selected files are restored. Files already equal to selected source content receive no writes. Empty directory selections record an empty manifest without requiring a destination directory.

Add refreshes and checks the dependency's effective selector against its locked revision. A moved branch, tag, or live filesystem fingerprint fails unless `--allow-outdated` is supplied; `--redefine` grants no freshness permission. Even with `--allow-outdated`, refresh and resolution must succeed and the locked source path must remain available. Filesystem sources retain no historical snapshots, so a changed live source cannot reproduce an old fingerprint.
Put global `--cache-dir DIRECTORY` before `add` to override the shared Git cache. Both add commands resolve its root for every backend; filesystem sources do not create cache data. Automation success emits one metadata-only `installation` record with operation `add`, status `unmodified`, and outcome `installed` or `unchanged`.

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
Help, version, and skill commands ignore unusable `--config` values.
