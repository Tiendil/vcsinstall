# `vcsinstall` Usage

`vcsinstall` is being developed to install files into a repository from other repositories, with lockfile and push-back support.
The current CLI provides help, version information, this usage document, configuration initialization, and configuration/lockfile loading.
`dependency add` is an empty operation that validates project files; dependency creation is not implemented yet.
Installation, lockfile writes, and push-back commands are not implemented yet.

## Commands

```bash
vcsinstall --help
vcsinstall version
vcsinstall skill usage
vcsinstall init
vcsinstall dependency add ci git+https://example.com/team/shared.git
```

`python -m vcsinstall` exposes the same CLI as the `vcsinstall` executable.
The `skill` command defaults to the `usage` document.
Help, version, and skill commands work without project configuration.
Init creates configuration; dependency add requires existing valid configuration.
The empty dependency add command accepts a name and repository but does not contact the repository, create declarations, or write lock state.

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
