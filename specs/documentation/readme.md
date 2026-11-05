# README Documentation

## Goal of the document

This document describes the root README's content, structure, and tone.

## Scope

This specification covers the root `README.md`.
The following are out of scope:

- detailed publishing procedures.
- generated help.
- detailed CLI/configuration syntax.
- agent development rules.
- other documentation.

## Audience

The README MUST primarily address humans discovering `vcsinstall`.
It MAY discuss coding agents but MUST NOT duplicate the full built-in agent skills.
It SHOULD quickly explain:

- the problem and vcsinstall's role.
- the current development status.
- how to try available commands.
- where to find detailed documentation.
- how to develop the project.

## Source Material

The README MUST reflect current `./specs/` behavior and architecture.
It MAY use `pyproject.toml` for package identity, repository links, and installation context.
Existing prose MUST NOT override conflicting specs or metadata.

The README MUST follow the availability and development-status requirements in Entry points in `specs/behavior/cli.md`.
Undecided command syntax and storage formats MUST NOT be invented for documentation examples.

## Structure

The README MUST start with one project-name h1, followed by introductory paragraphs that SHOULD explain its value plainly.
The introduction MUST identify the current development status before presenting usage examples.
These h2 sections MUST follow in order:

1. `Example`
2. `Features`
3. `Rationale`
4. `Quick Usage`
5. `Installation`
6. `Configuration`
7. `Specifications`
8. `Development`

`Features` MUST use compact bullets with bold feature names and short descriptions.
Additional h2 sections MUST NOT appear without updating this specification.
Helpful lower-level subsections MAY appear; deep implementation details SHOULD be avoided.

## Content Requirements

### Purpose and status

The README MUST explain that vcsinstall's intended purpose is installing files into a target repository from source repositories, with a lockfile and support for pushing local changes back to their source.
The rationale SHOULD explain why tracking the source of copied files and preserving a path for returning local changes are useful.
It MUST NOT imply support for particular version-control systems, source hosts, or synchronization policies without corresponding behavior specifications.

### Examples and quick usage

The `Example` section MUST demonstrate a currently available command.
The example SHOULD be short and explain any project state or source access it requires.

`Quick Usage` MUST illustrate:

- `vcsinstall --help`.
- `vcsinstall version`.
- `vcsinstall skill usage`.
- `vcsinstall cache clean`, once available.

It MUST mention that `python -m vcsinstall` exposes the same CLI.
It MUST explain the audiences for `human`, `llm`, and `automation` protocols, command-specific defaults, and placement of global options before subcommands.
It MUST show an explicit protocol example, such as `vcsinstall -p automation version`.

Human-facing examples SHOULD use command defaults.
Captured output MUST identify its protocol and follow the selected protocol's formatting.
Generated cell identifiers, when shown, MUST be identified as variable values or placeholders.
Command descriptions MUST link to `specs/behavior/cli.md`.
Detailed usage guidance MUST mention built-in skills or link to `specs/behavior/skill_fixtures.md`.

### Installation and configuration

The `Installation` section MUST state the supported Python version and installation source accurately.
Before a published package is available, it MUST show installation from a local checkout using both `uv` and `pip`.
PyPI installation examples MUST be included only when a published distribution is available.
Installation examples MUST make clear whether commands run on the host or through the Docker development environment.

The `Configuration` section MUST distinguish configuration-independent commands from commands that load or initialize a project under Application identity and project files in `specs/behavior/cli.md`.
It MUST identify the project configuration and lockfile filenames under `specs/behavior/config.md` and `specs/behavior/lockfile.md`, linking to those owning contracts when describing implemented project configuration.
Initialization guidance MUST follow `specs/behavior/cli.md` and its implementation status.

### Local storage and coordination

For implemented cache cleanup, the README MUST explain cache selection, project independence, and preservation of unrelated data under `specs/behavior/cache.md`.
For implemented project coordination, it MUST explain the lock's scope and waiting behavior under Project operation locking in `specs/behavior/cli.md`.
It MUST distinguish disposable cache data from persistent coordination files and the project's dependency lockfile.
It MUST clarify that changing or cleaning the cache does not change the project coordination location and that retained coordination files do not indicate a held lock.
Internal storage layouts and identity encodings SHOULD remain outside the README.

### Specifications and development

The README MUST identify `./specs/` as the behavior and architecture source and link to `specs/intro.md`.
It MUST link to `AGENTS.md` for agent development instructions.
It SHOULD identify the relationship to Donna, Depmesh, and `llm-tool-cli` without requiring familiarity with those projects.

The `Development` section MUST explain Docker Compose and uv development through the repository helpers.
It MUST show how to build the development image and run the CLI and tests inside the container.
It MUST identify the available formatting, static-analysis, and package-runtime checks without duplicating their implementation.

Development guidance MUST cover:

- running `@/workflows/polish.donna.md` through Donna.
- the purpose of the local Donna and Depmesh configurations.
- keeping task-local temporary files and workflow state in `.session/`.
- enabling the sibling `llm-tool-cli` checkout and updating the lockfile and development image together.
- maintaining the changelog using the tooling and artifacts in `specs/documentation/changelog.md`.

It SHOULD briefly identify the GitHub check and release workflows and the release environment and PyPI publishing prerequisites.
Detailed journal procedures, agent rules, and release-script implementation MUST remain outside the README.

## Style

The README SHOULD be:

- concise.
- practical.
- confident.
- accurate.

It SHOULD use short explanations, bullets, and commands.
It MUST avoid exhaustive manual content.
Detailed usage guidance SHOULD belong to built-in skills and specifications.
