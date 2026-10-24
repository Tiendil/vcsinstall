# vcsinstall

`vcsinstall` is being developed to install files into a target repository from source repositories, record their origins in a lockfile, and push local changes back to their source.

**Development status:** initial scaffold. Help, version output, and built-in usage documentation work today. File installation, lockfile management, and push back are planned.

## Example

After installing from a local checkout, run this on your host:

```bash
vcsinstall version
```

This prints the installed package version using the default human output protocol.

## Features

Available today:

- **CLI scaffold** — help, package version output, and built-in usage documentation.
- **Output protocols** — human output, framed output for coding agents, and JSON Lines for programs.
- **Python entry point** — the same commands through `vcsinstall` and `python -m vcsinstall`.

Planned:

- **File installation** — install files from source repositories into a target repository.
- **Lockfile** — record source revisions and installed files to reproduce an installation.
- **Push back** — transfer local changes to installed files back to their source repository.

## Rationale

Files shared between repositories can accumulate local edits while their sources continue to change. Keeping their origins and revisions recorded makes it easier to reproduce an installation. Returning useful local changes to the source lets other repositories benefit from them too.

Vcsinstall aims to support that workflow. Its product commands are still to be implemented.

## Quick Usage

With the package installed on your host:

```bash
vcsinstall --help
vcsinstall version
vcsinstall skill usage
```

`python -m vcsinstall` exposes the same CLI when the package is installed in that Python environment.

| Protocol | Audience |
| --- | --- |
| `human` | Terminal users |
| `llm` | Coding agents consuming framed output cells |
| `automation` | Programs consuming JSON Lines |

Version output defaults to `human`; skill documentation defaults to `llm`. Put global options before the command:

```bash
vcsinstall -p automation version
```

See the [CLI specification](specs/behavior/cli.md) for command details. `vcsinstall skill usage` prints the packaged usage guide; its documentation requirements are in the [skill fixture specification](specs/behavior/skill_fixtures.md).

## Installation

Requires Python `>=3.12,<4.0`. Install the development version from a local checkout using either method below. Run these commands on your host from the repository root.

With uv:

```bash
uv tool install .
```

With pip, inside an active Python virtual environment:

```bash
python -m pip install .
```

For container development, follow [Development](#development).

## Configuration

The available help, version, and skill commands work without project configuration. The shared `--config` option is accepted, but these commands do not load a project configuration file.

The default project configuration filename is `vcsinstall.toml`; the lockfile filename is `vcsinstall.lock`.
The configuration schema and lockfile format are still to be defined.

## Specifications

The [specification index](specs/intro.md) is the starting point for the project's behavior and architecture requirements. Shared terminology is defined in the [dictionary](specs/dictionary.md).

Agents should read [AGENTS.md](AGENTS.md) before working on the project.

## Development

Development uses Docker Compose, Python 3.12, and uv. Run these helpers on your host from the repository root; they execute development commands inside the container:

```bash
./bin/dev-build-containers.sh
./bin/dev.sh uv run -- vcsinstall --help
./bin/dev-tests.sh
```

The image installs dependencies from `uv.lock` and matches the host user's UID and GID. The repository is mounted at `/repository`; the virtual environment stays inside the image. Use `./bin/dev.sh <command>...` for other container commands.

| Helper | Purpose |
| --- | --- |
| `./bin/dev-format.sh` | Apply formatting and unused-code cleanup |
| `./bin/dev-check-formatting.sh` | Check formatting without changing files |
| `./bin/dev-check-semantics.sh` | Check architecture, spelling, lint, and types |
| `./bin/dev-build-package.sh` | Build the source distribution and wheel |
| `./bin/dev-check-runtime.sh` | Build and check the installed wheel outside the source tree |

### Project tools

[Donna](https://github.com/Tiendil/donna) runs deterministic development workflows. Its local configuration is [donna.toml](donna.toml). Run the complete polishing workflow to format the code and check architecture, spelling, lint, types, and tests:

```bash
./bin/dev.sh uv run -- donna -p llm run @/workflows/polish.donna.md
```

[Depmesh](https://github.com/Tiendil/depmesh) discovers relationships between project artifacts. [depmesh.toml](depmesh.toml) connects code with its specifications, tests, imports, and callers.

Keep task-local temporary files under the ignored `.session/` directory, following the [session skill](.agents/skills/session/SKILL.md). Donna workflow state and project journal records live there too.

### Shared CLI library

[llm-tool-cli](https://github.com/Tiendil/llm-tool-cli) provides the reusable CLI foundations used by Vcsinstall, Donna, and Depmesh. The default setup uses its published distribution.

To develop with the sibling `../llm-tool-cli` checkout, uncomment the matching local-source settings in [pyproject.toml](pyproject.toml), [docker-compose.yml](docker-compose.yml), and [docker/Dockerfile](docker/Dockerfile). Then update the lockfile and rebuild the development image together:

```bash
./bin/dev.sh uv lock
./bin/dev-build-containers.sh
```

### Changelog and releases

Record notable changes in [changes/unreleased.md](changes/unreleased.md), then regenerate [CHANGELOG.md](CHANGELOG.md) with Changy:

```bash
./bin/dev.sh uv run -- changy changelog create
```

The [code-check workflow](.github/workflows/code-checks.yaml) runs development checks on pull requests. The manual [release workflow](.github/workflows/release.yaml) checks the project, prepares a release, and publishes distributions to PyPI. Publishing requires a configured GitHub `release` environment, PyPI trusted publishing for that workflow, and permission to push release commits and tags.
