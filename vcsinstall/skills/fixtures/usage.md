# `vcsinstall` Usage

`vcsinstall` is being developed to install files into a repository from other repositories, with lockfile and push-back support.
The current CLI provides help, version information, this usage document, configuration initialization, and dependency declaration and locking.
`dependency add` supports Git and ordinary filesystem sources, including protected replacement of existing dependency entries.
Entry creation, other dependency operations, general installation, and push-back commands are not implemented yet.

## Commands

```bash
vcsinstall --help
vcsinstall version
vcsinstall skill usage
vcsinstall init
vcsinstall dependency add ci 'git+https://example.com/team/shared.git#branch=main'
```

`python -m vcsinstall` exposes the same CLI as the `vcsinstall` executable.
The `skill` command defaults to the `usage` document.
Help, version, and skill commands work without project configuration.
Init creates configuration; dependency add requires existing valid configuration.
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
