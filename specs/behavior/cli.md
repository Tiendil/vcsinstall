# Command-line interface

## Goal of the document

This document describes Vcsinstall's command-line interface contract.

## Scope

This specification covers the development scaffold's executable interface and shared utility commands.
The following are out of scope:

- installation and synchronization commands.
- lockfile format.
- push-back behavior.
- project configuration schemas.

## Entry points

The `vcsinstall` executable and `python -m vcsinstall` MUST expose the same commands.
`--help` and `-h` MUST provide usage help.
The development scaffold MUST NOT expose placeholder installation, lockfile, or push-back commands.

## Project files

The default project configuration filename MUST be `vcsinstall.toml`.
The project lockfile filename MUST be `vcsinstall.lock`.
Help, version, and skill commands MUST NOT load or create either project file.

## Global options

Global options MUST precede the subcommand.

- `--protocol` / `-p` MUST select `human`, `llm`, or `automation` output.
- `--config` MUST be accepted under the shared CLI contract, but MUST NOT cause utility commands to load project configuration.

The protocol and output behavior MUST follow the corresponding contracts in `llm-tool-cli`.

## Version

`vcsinstall version` MUST report the installed distribution version.
It MUST default to human output and support explicit LLM and automation protocols.
It MUST work without project configuration.

## Skills

`vcsinstall skill [document]` MUST print packaged Markdown documentation.
The default and required document MUST be `usage`.
Skill output MUST default to the LLM protocol and support explicit human and automation protocols.
The command MUST work without project configuration.
Unknown document names MUST fail with usage guidance.

## Diagnostics

Expected execution failures MUST use the selected protocol and nonzero exit status under the shared CLI error contract.
Successful commands MUST exit with status zero.
Unexpected technical failures MUST remain distinct from expected operational failures.
