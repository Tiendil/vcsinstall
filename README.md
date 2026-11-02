# vcsinstall

`vcsinstall` is being developed to install files into a target repository from source repositories, record their origins in a lockfile, and push local changes back to their source.

**Development status:** `init` creates project configuration, `dependency add` declares and locks Git and filesystem dependencies, `dependency remove` removes unused dependencies, `add` installs file and directory entries at their locked revisions, and `remove` removes entries with local-change protection or retains their files as unmanaged content. Help, version output, and built-in usage documentation are available. Other dependency commands, general installation, and push back are planned.

## Example

After installing from a local checkout, run this on your host:

```bash
vcsinstall version
```

This prints the installed package version using the default human output protocol.

## Features

Available today:

- **CLI scaffold** — help, package version output, and built-in usage documentation.
- **Project configuration** — create a starter with `init`, discover or explicitly select configuration, and validate configuration and existing lock state.
- **Dependency locking** — add Git or filesystem sources, select branches, tags, or immutable revisions, and explicitly redefine dependencies while protecting local edits.
- **Dependency removal** — remove unused dependency declarations, revision locks, and obsolete baselines while preserving installed files.
- **Entry installation** — install files or complete directories from a dependency's locked revision, rename destinations, and explicitly redefine entries with local-change protection.
- **Entry removal** — remove selected entries and verified content, explicitly discard local changes with `--force`, or retain files with `--keep-files`.
- **Source detection** — recognize Git repositories before ordinary directories, probe unqualified remote URLs, and honor explicit backend choices.
- **Output protocols** — human output, framed output for coding agents, and JSON Lines for programs.
- **Python entry point** — the same commands through `vcsinstall` and `python -m vcsinstall`.

Planned:

- **Project installation** — reproduce every declared entry and reconcile missing dependency locks.
- **Push back** — transfer local changes to installed files back to their source repository.

## Rationale

Files shared between repositories can accumulate local edits while their sources continue to change. Keeping their origins and revisions recorded makes it easier to reproduce an installation. Returning useful local changes to the source lets other repositories benefit from them too.

vcsinstall is building that workflow incrementally, starting with dependency declaration and locking.

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

Create a starter [configuration](specs/behavior/config.md) in the current directory:

```bash
vcsinstall init
vcsinstall --config project/custom.toml init
```

The parent directory must already exist. Init refuses to overwrite an existing file and creates no lockfile.

Declare and lock a source dependency:

```bash
vcsinstall dependency add ci 'git+https://example.com/team/shared.git#branch=main'
vcsinstall dependency add local-guides file:///home/user/shared-guides
vcsinstall dependency add --redefine ci 'git+https://example.com/team/shared.git#tag=v2.0'
```

Install entries from a locked dependency:

```bash
vcsinstall add ci@/specs @/specs/shared
vcsinstall add ci@/LICENSE @/licenses/shared.txt
vcsinstall add --redefine ci@/guides @/docs/shared
vcsinstall add --allow-outdated ci@/examples @/examples/shared
```

`NAME@/PATH` selects a file or directory within a declared dependency; `NAME@/` selects its repository root. The destination is the exact final path, using `@/` project identifiers or contained filesystem paths relative to the invocation directory. Add requires a matching dependency lock and preserves its revision. It checks the current source selector before installation and requires `--allow-outdated` when that selector has moved. This permission still requires a successful freshness check and availability of the locked content. An existing entry destination requires `--redefine`, including an identical mapping; redefinition preserves local-content protection and can change the owning dependency.

Remove declared entry roots:

```bash
vcsinstall remove @/specs/shared @/licenses/shared.txt
vcsinstall remove --keep-files @/docs/shared
vcsinstall remove --force @/examples/shared
```

Removal checks every selected entry before changing files. Modified or unverified existing content requires `--force` for deletion. `--keep-files` preserves all content as unmanaged project files and takes precedence over `--force`. Missing installed content does not prevent removal. Dependency declarations and their shared revision locks remain, including after their last entry is removed. Removal needs no source access, Git client, or repository cache.

Remove an unused dependency after removing or reassigning its entries:

```bash
vcsinstall dependency remove ci
```

Dependency removal deletes its declaration, shared revision lock, and obsolete entry baselines. It preserves installed content and unrelated records, works with missing lock state, and requires no source or cache access. A baseline still protecting a reassigned entry must be reconciled before removing its old dependency.

Without `--config`, loading discovers the nearest `vcsinstall.toml` in the current directory or its parents. An explicit path overrides discovery. The selected configuration's parent is the project root, and its fixed sibling `vcsinstall.lock` is the [lockfile](specs/behavior/lockfile.md). A missing lockfile is allowed; an invalid existing lockfile fails validation. Loading changes neither file and does not access sources or installed contents.

Dependency add requires existing configuration and writes both the declaration and its resolved revision. It creates no entry mappings. Git repositories share a per-user cache; global `--cache-dir DIRECTORY` selects another cache root. Dependency add resolves the cache root for every backend. Filesystem sources use their live directory and do not create cache data.

When redefining a dependency with entries, each entry owns its entire destination tree. Extra local files count as local changes and can block replacement. After validation, changed entries are removed and recopied in full; entries already matching the source stay untouched. Keep generated and unrelated files outside these destinations.

Configuration and locks store version selection as a kind/value object, for example `selector = { kind = "branch", value = "main" }`. The lock's separate `revision` records the resolved immutable content ID.

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

[Donna](https://github.com/Tiendil/donna) runs deterministic development workflows. Its local configuration is [donna.toml](donna.toml). Run the host-installed Donna executable to format the code and check architecture, spelling, lint, types, and tests through the container helpers:

```bash
donna -p llm run @/workflows/polish.donna.md
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
