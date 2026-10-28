# `vcsinstall` Usage

`vcsinstall` is being developed to install files into a repository from other repositories, with lockfile and push-back support.
The current development scaffold provides help, version information, and this usage document.
Installation, lockfile management, and push-back commands are not implemented yet.

## Commands

```bash
vcsinstall --help
vcsinstall version
vcsinstall skill usage
```

`python -m vcsinstall` exposes the same CLI as the `vcsinstall` executable.
The `skill` command defaults to the `usage` document.
These commands work without project configuration.

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

The shared `--config` option is accepted, but the current commands do not load project configuration.

The default project configuration filename is `vcsinstall.toml`; the lockfile filename is `vcsinstall.lock`.
The version 1 configuration schema and lockfile format are specified; loading and managing these files remain under development.
