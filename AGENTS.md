# Instructions for AI Agents

This document contains project-specific instructions for agents working on `vcsinstall`.

Every agent MUST follow these instructions.

## Project Overview

`vcsinstall` is a Python CLI tool for installing files into a repository from other repositories, with lockfile and push-back support as its intended product scope.

The repository currently provides the development scaffold and shared CLI commands. Do not implement installation, lockfile, or push-back behavior without a corresponding development request.

## Source Of Truth

Project behavior and architecture are specified in `./specs/`.

Agents MUST read the relevant specifications before making changes.

Start from `./specs/intro.md` to find the relevant specification documents.

When adding, deleting, or significantly changing a specification, agents MUST update `./specs/intro.md`.

Agents MUST NOT create new specifications without explicit instructions.

Agents MUST NOT delete or significantly change existing specifications without explicit instructions.

## Development Environment

All development-related operations MUST be performed in Docker Compose containers through the repository helpers.

Agents MUST NOT perform development-related operations directly on the host machine.

Allowed development commands:

- `./bin/dev.sh` — run development utilities inside the container, for example `./bin/dev.sh uv run -- pytest`.
- `./bin/dev-build-containers.sh` — build the development image from `uv.lock`; use after approved Docker or dependency changes.
- `./bin/dev-format.sh` — apply Python formatting and unused-code cleanup.
- `./bin/dev-check-formatting.sh` — check Python formatting.
- `./bin/dev-check-semantics.sh` — check architecture, spelling, lint, and types.
- `./bin/dev-tests.sh [pytest arguments...]` — run Python tests.
- `./bin/dev-build-package.sh` — build the source distribution and wheel.
- `./bin/dev-check-runtime.sh` — check the built wheel outside the source tree.

After authorized dependency changes, run `./bin/dev.sh uv lock` and rebuild the development image.

Code and file searches MUST run on the host using host-installed tools, including `ast-grep` and `rg`, without Docker or development-helper wrappers.
Reading and editing repository files MAY also be done on the host machine.
The project journal helpers use the development container so agent and Donna records share the same Taskwarrior environment.

## Restricted Changes And Operations

The following operations require explicit authorization from the current task or earlier session instructions:

- Change `docker-compose.yml` or Docker-related configuration.
- Change Docker runtime parameters such as resources or volumes.
- Change running Docker services unrelated to this project.
- Install new dependencies.
- Update lock files.
- Install new tools, utilities, or software on the host machine or in development containers.
- Change project structure by moving files or creating new top-level directories.

Proceed with changes already authorized by the task; do not request approval again.
If a required operation is not already authorized, ask for explicit permission before doing it.

## Implementation Guidance

Follow existing specifications and local project patterns.

Keep changes scoped to the requested task.

Do not implement behavior that is only mentioned as future or possible functionality unless explicitly requested.

Use `llm_tool_cli` directly for shared CLI infrastructure rather than duplicating its behavior.
Keep the package structure limited to modules with current responsibilities.

When code is added, tests SHOULD follow `./specs/architecture/tests.md`.

When entities, errors, warnings, or module layout are affected, agents MUST check the corresponding architecture specs.

## Tool Priority

### `donna`

Use Donna to run project-local deterministic workflows when the developer, these instructions, or an active Donna workflow explicitly asks for one.

Donna controls workflow state only. Agents remain responsible for reading project instructions, using `depmesh` where applicable, editing files, running checks, and reporting results.

Before using Donna for the first time in a session, read its usage and list workflows:

```bash
./bin/dev.sh uv run -- donna -p llm skill usage
./bin/dev.sh uv run -- donna -p llm list
```

Use Donna's `llm` protocol for agent-facing commands unless a human explicitly asks for another protocol.

Donna is configured to log significant operation steps via the `task` tool.

Special workflows to use:

- `@/workflows/polish.donna.md` — format, fix architecture, lint, and test errors. Run it after making changes to the codebase at the moments when the project is expected to be in a working state: between significant implementation steps, before reporting completion of a task, etc. Run this workflow instead of running individual operations, unless a specific operation is needed for a particular reason.

```bash
./bin/dev.sh uv run -- donna -p llm run @/workflows/polish.donna.md
```

Do not run `donna -p llm new-session` unless the developer explicitly asks to reset or start a fresh Donna session.

### `depmesh`

Use `depmesh` to discover dependencies between project artifacts.

Agents MUST use `depmesh` for dependency types supported by its configuration.

Run `depmesh` through the development container.
At the start of each work session, read its usage instructions:

```bash
./bin/dev.sh uv run -- depmesh -p llm skill usage
```

Query affected files for governing specifications, related tests, imports, and reverse imports when those relationships are relevant.

### `ast-grep`

`ast-grep` is a tool for searching and manipulating Abstract Syntax Trees in code.
Run the host-installed executable directly, for example `ast-grep --help`.

You MUST use it to:

- Search for specific code patterns or structures in the codebase.
- Extract information from code, such as function definitions, variable declarations, or specific code constructs.
- Analyze code for specific patterns or anti-patterns.
- Refactor particular code patterns or structures across the codebase.
- Introduce small behaviors or features into existing code.

You MUST NOT use it for implementing large features that require substantial new code.

### `rg`

Use host-installed `rg` directly for text and file searches unless a structural code query is needed.

`ast-grep` has a higher priority than `rg` whenever a structural code query is needed.

### `task`

`task` — Taskwarrior — is the project journal for significant agent-side work.

You MUST use it to write journal records with these exact command templates from the project root:

```bash
./bin/taskwarior.sh log +journal +agent kind:goal "<goal description>"
./bin/taskwarior.sh log +journal +agent kind:step "<phase progress or completion handoff>"
./bin/taskwarior.sh log +journal +agent kind:thought "<important thought>"
./bin/taskwarior.sh log +journal +agent kind:assumption "<important assumption>"
./bin/taskwarior.sh log +journal +agent kind:change "<what changed and where>"
```

Journal messages MUST be single-line strings.

You MUST log:

- Goals of long-running agent-side operations with `kind:goal`.
- Significant steps of long-running operations with `kind:step`.
- Significant thoughts during long-running operations with `kind:thought`.
- Significant assumptions during long-running operations with `kind:assumption`.
- Changes in project source code or project structure with `kind:change`.

You MAY add extra tags after `+agent` and before the message:

```bash
./bin/taskwarior.sh log +journal +agent kind:<message-kind> +<extra-tag>... "<single-line journal message>"
```

For each non-trivial Donna action request or long-running agent task:

1. Write exactly one `goal` record at action-request or task start.
2. Write `step` records at significant phase boundaries.
3. Write `change` records after each meaningful source update batch.
4. Write one final `step` record immediately before reporting completion or handing work back to the developer.

You MUST consider these cases significant phase boundaries:

- A work phase expected to take more than 10 seconds.
- Transition from analysis or research to implementation.
- Transition to a new step in a multi-step process.
- Start or completion of a multi-file or multi-artifact change batch.
- A decision that changes implementation direction.

You MUST NOT log CLI commands or elementary steps.

You can read the logged journal with:

```bash
./bin/journal-tail.py --lines 20
```
