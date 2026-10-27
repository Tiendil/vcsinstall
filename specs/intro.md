# List of specifications

## Goal of the document

This document indexes the project's specifications and their purposes.

## Scope

The specification index and its maintenance; detailed product requirements are out of scope.

## Index requirements

The index MUST list every specification with its path and a brief description.
Each description MUST identify the specification's stable subject or purpose rather than enumerate its contents.
Descriptions MUST NOT list individual requirements or current implementation details.
Descriptions SHOULD remain accurate when details change within the specification's existing scope.
The index MUST be kept complete and accurate when specifications are added, removed, or change scope.

**Example:** Describe a configuration specification as “The project configuration contract,” without listing its current configuration fields.

## Specification directories

- `./specs/` — all specifications.
- `./specs/architecture/` — system architecture.
- `./specs/behavior/` — observable tool behavior.
- `./specs/behavior/vcs/` — source backend behavior.
- `./specs/behavior/hosting/` — outgoing delivery and hosted-service behavior.
- `./specs/documentation/` — documentation artifacts.
- `./specs/meta/` — specification-writing requirements.

## Specification documents

- `./specs/intro.md` — Index of project specifications.
- `./specs/dictionary.md` — Shared project terminology.
- `./specs/architecture/entities.md` — Conventions for typed project data and boundary models.
- `./specs/architecture/errors.md` — Conventions for representing and propagating diagnostics.
- `./specs/architecture/modules_layout.md` — Project module responsibilities and dependency boundaries.
- `./specs/architecture/naming.md` — Project and code naming conventions.
- `./specs/architecture/tests.md` — Requirements for project tests.
- `./specs/behavior/cache.md` — The shared repository cache contract.
- `./specs/behavior/cli.md` — The command invocation, selection, and result contract.
- `./specs/behavior/config.md` — The project declaration and configuration contract.
- `./specs/behavior/files-and-directories.md` — The file and directory management contract.
- `./specs/behavior/locking.md` — The dependency revision and installation state contract.
- `./specs/behavior/lockfile.md` — The persisted installation state format and file I/O contract.
- `./specs/behavior/skill_fixtures.md` — Requirements for built-in skill documentation.
- `./specs/behavior/vcs-interaction.md` — The shared repository access and change delivery contract.
- `./specs/behavior/vcs/git.md` — The Git source contract.
- `./specs/behavior/vcs/filesystem.md` — The filesystem source contract.
- `./specs/behavior/vcs/noop.md` — The test-only noop source contract.
- `./specs/behavior/hosting/git.md` — The native Git delivery contract.
- `./specs/behavior/hosting/github.md` — The GitHub contribution and review contract.
- `./specs/behavior/hosting/filesystem.md` — The filesystem delivery contract.
- `./specs/behavior/hosting/noop.md` — The test-only noop hosting contract.
- `./specs/documentation/changelog.md` — Requirements for maintaining the project changelog.
- `./specs/documentation/readme.md` — The root README's documentation contract.
- `./specs/meta/general.md` — Requirements for specification content and presentation.
- `./specs/meta/behavior.md` — Rules for expressing observable behavior in specifications.

Shared CLI and infrastructure contracts adopted by vcsinstall belong to the corresponding specifications in `llm-tool-cli`.
This index lists vcsinstall's specifications, which define their application and tool-specific behavior.
