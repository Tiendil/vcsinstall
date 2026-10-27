# Skill Fixtures

## Goal of the document

This document describes packaged Markdown documentation printed by `vcsinstall skill`.

## Scope

This specification covers built-in skill fixtures.
The following are out of scope:

- exact prose.
- CLI parsing.
- rendering.
- external agent-skill installation.

## Availability

Built-in skill documentation MUST be included in the installed distribution and be available without project configuration.
Every document offered by the skill command MUST have packaged Markdown content.
Each fixture MUST start with one h1 identifying vcsinstall and the document's subject.

## Usage fixture

Usage MUST describe the implemented interface under `specs/behavior/cli.md`, including:

- currently implemented commands.
- command invocation and output protocols.
- placement of global options before subcommands.
- which commands require project configuration and which interfaces remain project-independent.
- the configuration and lockfile names.
- the development status of installation, lockfile, and push-back functionality.

Examples MUST use implemented behavior.
The fixture SHOULD be concise and self-contained, without requiring source or specification inspection for usage.
