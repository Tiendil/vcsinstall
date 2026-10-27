# Command-line interface

## Goal of the document

This document describes vcsinstall's complete command-line interface, including inputs, results, and observable command effects.

## Scope

This specification covers command invocation, output, and observable effects at the CLI boundary.
The following are out of scope:

- configuration and lockfile schemas.
- shared repository identity and source-path semantics.
- revision locking, content hashing, and baseline state transitions.
- internal cache lifecycle and checkout organization.
- adapter architecture.
- hosting-provider APIs and provider-specific review workflows.
- exact skill prose.

## Dictionary

- `NAME` — a project-local dependency name.
- `REPOSITORY` — a locator identifying a backend and native repository locator, optionally carrying a version fragment for `dependency add`.
- `SOURCE` — a `dependency://NAME/PATH` URL selecting a repository path within a declared dependency.
- `SOURCE_PATH` — a file or directory path relative to the source repository root.
- `DESTINATION` — the final project path at which `add` installs an entry.
- `ENTRY_PATH` — a local path selecting a declared entry by its destination, including when its installed content is missing.
- `REF` — an untyped source-version reference accepted in a repository URL fragment.
- `REVISION` — an immutable source revision identifier under the selected backend's contract.

## Entry points

The `vcsinstall` executable and `python -m vcsinstall` MUST expose the same commands.
The root MUST expose a command group with global options before the subcommand:

```text
vcsinstall [GLOBAL_OPTIONS] COMMAND [COMMAND_OPTIONS] [ARGUMENTS]
```

Invoking the root MUST NOT perform project operations implicitly.
Root and command `--help` / `-h` MUST follow the Application setup contract in `specs/behavior/cli.md` in `llm-tool-cli`.
Shell completion MUST follow that same contract.

This specification describes the intended interface independently of implementation progress.
Commands MAY be introduced incrementally, but an exposed command MUST implement its advertised behavior rather than act as a placeholder.
Help and packaged usage documentation MUST distinguish available behavior from planned capabilities.

## General behavior

vcsinstall MUST treat installed content as files and directories without requiring package metadata or imposing a skill, specification, or other application-specific layout.
Commands MUST NOT create commits or branches in the target repository or require it to use the same VCS as its sources.
Command execution MUST NOT require a vcsinstall-specific interactive prompt or launch a commit-message editor.

The required commands MUST be:

- `init` — Create starter project configuration.
- `dependency add [OPTIONS] NAME REPOSITORY` — Declare and lock a named dependency.
- `dependency list` — List declared dependencies, their shared locked revisions, and local entry statistics.
- `dependency outdated [NAME...]` — Check all or named dependencies for changes in their configured source versions and report differing locked and current revisions.
- `dependency remove NAME` — Remove an unused dependency declaration and its lock.
- `dependency update [NAME...]` — Advance all or named dependencies within their configured version selectors and refresh each dependency's entries together.
- `dependency push --mode MODE [OPTIONS] NAME [ENTRY_PATH...]` — Deliver changes from all or selected entries of one named dependency through the explicit mode.
- `add [OPTIONS] SOURCE DESTINATION` — Declare and install one entry from a dependency's locked revision.
- `install [OPTIONS]` — Install the entries declared by the project, preferring their locked revisions.
- `remove [OPTIONS] ENTRY_PATH...` — Remove declared entries and their installed content, with `--keep-files` retaining the content as unmanaged project files.
- `list [ENTRY_PATH...]` — Inspect declared installations and their local changes.
- `cache clean` — Clear disposable data from the selected shared cache.
- `skill [DOCUMENT]` — Print built-in documentation.
- `version` — Print the installed tool version.

Locking operations and their state changes MUST follow Operation contracts and Preparation and consistency in `specs/behavior/locking.md`.

## Application identity and project files

LLM cell framing MUST use `VCSINSTALL`, including shared errors produced during argument validation.
Project configuration selection, project-root derivation, and validation MUST follow `specs/behavior/config.md`.
Project lockfile selection and representation MUST follow `specs/behavior/lockfile.md`.

The following commands MUST select and validate existing project configuration before performing their requested operation:

- `add`.
- `dependency add`.
- `dependency list`.
- `dependency outdated`.
- `dependency remove`.
- `dependency update`.
- `dependency push`.
- `install`.
- `remove`.
- `list`.

Missing configuration MUST fail with the shared diagnostic rather than implicitly initialize a project.

The following interfaces MUST NOT load or create project files or require a cache or a VCS client:

- root and command help.
- shell completion.
- `version`.
- `skill`.

`cache clean` MUST follow the project-file and client independence requirements in Cleanup in `specs/behavior/cache.md`, remaining usable outside a project and with an unusable `--config` value.

## Global options

Global options MUST precede the subcommand.

### `--protocol PROTOCOL` / `-p PROTOCOL`

The protocol option MUST follow the Protocol option and Protocol selection contracts in `specs/behavior/cli.md` in `llm-tool-cli`.

### `--config PATH`

Parsing MUST follow the Configuration option contract in `specs/behavior/cli.md` in `llm-tool-cli`, deferring filesystem validation until the configuration is used.
An explicit path MUST override configuration discovery without fallback to another project.
Configuration-independent commands MUST remain usable with an unusable `--config` value.

### `--cache-dir DIRECTORY`

Cache-root selection and Cache-independent operations in `specs/behavior/cache.md` MUST govern path interpretation, defaults, and deferred cache access.
Repository identity and layout and Repository reuse and revision isolation in that specification MUST govern cached repositories and their source content.

## Dependency definitions and source arguments

### Named dependencies

Dependency names and declarations MUST follow `specs/behavior/config.md`, and terminology MUST follow `specs/dictionary.md`.
Shared revision and baseline semantics MUST follow State and shared revisions in `specs/behavior/locking.md`.

### Repository arguments

Repository argument normalization MUST follow Repository locators and identity in `specs/behavior/vcs-interaction.md` and the selected backend's contract.

`REPOSITORY` MUST accept VCS-qualified URLs of the form `<vcs>+<transport>://...`, including `git+https`, `git+ssh`, and `git+file` for Git.
The VCS prefix MUST select the backend, and the remainder MUST identify the native repository locator using that backend's transport semantics.
Native schemes that identify a VCS, such as `git://`, MUST select that VCS when supported.
An explicit `--vcs` on `dependency add` MUST select the VCS for an unqualified native locator or agree with a VCS already identified by the scheme; a conflict MUST fail as an invalid argument.
Without an explicit VCS, unqualified local paths and scp-style repository addresses MUST default to Git.
Other transport-only URL schemes, such as `ssh://`, MUST require an explicit `--vcs` on `dependency add` or a VCS-qualified scheme.

Without an explicit `--vcs`, plain `file://`, `http://`, and `https://` locators MUST identify ordinary filesystem or HTTP sources rather than imply Git.
Filesystem source behavior MUST follow `specs/behavior/vcs/filesystem.md`.
HTTP backend support MAY be introduced separately; until supported, HTTP source inputs MUST fail explicitly with guidance to use a VCS-qualified URL when a VCS repository was intended.
A `.git` suffix or a familiar hosting domain MUST NOT override this distinction.
For example, `git+file:///home/user/shared` MUST identify a Git repository, while plain `file:///home/user/shared` MUST identify ordinary filesystem content.

For example, Git's `git+ssh://...` MUST normalize to native `ssh://...`, while an SVN `svn+ssh://...` locator MUST retain the native SVN-over-SSH scheme when passed to its client.
URL normalization MUST separate vcsinstall's source-version fragment from the native repository locator.

Relative native repository paths MUST resolve against the invocation's working directory; the recorded location MUST retain that meaning on later invocations from other directories.

### Source-version fragments and option aliases

For `dependency add`, a repository URL fragment MUST accept one of:

- `#branch=BRANCH` — Track the named branch.
- `#tag=TAG` — Select the named tag.
- `#rev=REVISION` — Pin an immutable source revision.
- `#REF` — Infer a branch, tag, or immutable revision from a bare reference.

Typed selector values MUST be nonempty and retain their literal spelling and case after percent decoding.
Literal fragment delimiters in repository URLs or selector values MUST be percent-encoded.
Selector decoding MUST occur exactly once and MUST preserve literal `+` characters.
An empty fragment, unknown selector kind, malformed escape, or combination of selectors MUST fail as an invalid argument.
Unescaped `&` MUST NOT combine selectors; a literal `&` in a selector value MUST be percent-encoded.
Fragment syntax MUST apply only to URL-form locators; unqualified filesystem paths and scp-style addresses MUST retain their native literal meaning.

A bare reference MUST resolve unambiguously to one supported selector kind and value.
If a branch and tag share the supplied name, the reference MUST fail with guidance to use a typed selector, even when both point to the same revision.
Missing or otherwise ambiguous references MUST fail before changing project files.
Resolved selector behavior MUST follow Version selection in `specs/behavior/locking.md`, and recorded declarations MUST follow `specs/behavior/config.md`.

Dependency add MUST accept these source options:

- `--vcs VCS` — Select or assert the VCS under the locator rules above.
- `--branch BRANCH` — Alias for the `branch` fragment selector.
- `--tag TAG` — Alias for the `tag` fragment selector.
- `--rev REVISION` — Alias for the `rev` fragment selector.

These options MUST NOT be required when the URL supplies the backend and version selection.
`--branch`, `--tag`, and `--rev` MUST be mutually exclusive and MUST NOT be combined with a version fragment, even when the selections appear equivalent.
Equivalent URL-fragment and option forms MUST produce the same normalized declaration and selector semantics.

Without a version fragment or option, `dependency add`, including with `--replace`, MUST use default-version inference under Version selection in `specs/behavior/locking.md`.
Revision arguments MAY use abbreviations accepted under that contract.
CLI-generated declarations MUST follow the separate repository and selector fields in `specs/behavior/config.md`.

Explicit source options without source arguments MUST be rejected.

### Backend support and native semantics

Backend selection and capabilities, Native client execution, and Version resolution and anchoring in `specs/behavior/vcs-interaction.md` MUST govern source interaction.
Each source backend MUST follow its contract for selectors and extraction from an available repository view.
Repository acquisition, refresh, and outgoing workflows MUST follow the hosting contracts and selection rules in `specs/behavior/vcs-interaction.md`.
Unversioned-source behavior MUST NOT silently weaken the locking and publication contracts of VCS-backed dependencies.

### Source arguments

`SOURCE` MUST use `dependency://NAME/PATH`, resolving `NAME` only against declared project dependencies.
Unknown names MUST fail before fetching sources or changing project files.
The authority MUST contain only the exact dependency name, without user information or a port.
Query parameters and fragments MUST NOT be accepted.
Raw repository URLs and per-entry version options MUST NOT be accepted by `add`.
The path after the authority separator MUST be percent-decoded exactly once as `SOURCE_PATH`, preserving literal `+` characters.
An empty path, as in `dependency://ci/`, MUST select the repository content root, equivalent to `.`.
Malformed escapes MUST fail explicitly.
The decoded `SOURCE_PATH` MUST satisfy Source paths in `specs/behavior/vcs-interaction.md`.

## Local path arguments and entry selection

`DESTINATION` and `ENTRY_PATH` arguments MUST follow the Mixed path normalization contract in `specs/behavior/paths.md` in `llm-tool-cli`, using the selected project root and the invocation's working directory as the base for relative filesystem inputs.
They MUST accept canonical `@/` identifiers and contained filesystem paths, including absolute paths and paths whose targets do not yet exist.
Shared path diagnostics MUST retain their native information.

Destination validity and entry identity MUST follow Destinations and entry identity in `specs/behavior/config.md`.
`DESTINATION` MUST identify the exact final file or directory path; an existing directory MUST NOT cause an implicit source-basename suffix to be appended.
A directory mapping MUST place its selected content directly under that destination.
Add MUST allow files and directories to be installed with names and paths different from those in the source repository.

For `list`, omitted entry paths MUST select every declared entry.
Remove MUST require at least one explicit `ENTRY_PATH` and MUST NOT infer a selection when none is supplied.
`dependency push` MUST restrict entry-path selection to its named dependency under the rules below.
Explicit entry paths MUST select exactly the matching declared entry roots by their canonical destination paths, without implicit descendant selection or glob expansion.
Selecting an entry MUST NOT require its installed content to exist.
Inputs normalizing to the same entry MUST select it once.
Every explicit entry path MUST be validated before fetching sources, changing project files, or publishing changes.
An unknown entry path MUST fail instead of silently broadening or ignoring the selection.
The standard `--` argument separator MUST allow positional values beginning with `-`.

## `vcsinstall init` command

Init MUST follow the Initialization command contract in `specs/behavior/cli.md` in `llm-tool-cli`, using the default filename and configuration contract in `specs/behavior/config.md`.
Starter contents MUST follow the Starter configuration contract in `specs/behavior/config.md`.
Its effects on source access and lock state MUST follow State and shared revisions in `specs/behavior/locking.md`.

## `vcsinstall dependency` command group

Bare `dependency` MUST show group help without loading project files or requiring a cache or VCS client.
The required subcommands MUST be:

- `add`.
- `list`.
- `outdated`.
- `remove`.
- `update`.
- `push`.

### `vcsinstall dependency add` command

Dependency add MUST require exactly one name and one repository locator.
It MUST normalize the repository and version selection under the source argument rules above and invoke the `dependency add` operation contract in `specs/behavior/locking.md`.
It MUST NOT create entry mappings implicitly.
Names and command shape MUST be validated before fetching sources or changing project files.

Without `--replace`, an already declared name MUST fail as an invalid argument, including an identical repeat.
The error MUST identify the name and explain that intentional redefinition requires `--replace`.
`--replace` MUST authorize explicit dependency replacement under that operation contract.
For a new name, `--replace` MUST still create the dependency normally.

Examples:

```bash
vcsinstall dependency add ci 'git+https://example.com/team/shared.git#branch=main'
vcsinstall dependency add formatting 'git+https://example.com/team/shared.git#tag=v1.0'
vcsinstall dependency add --vcs git --branch main ci-native https://example.com/team/shared.git
vcsinstall dependency add --replace formatting 'git+https://example.com/team/shared.git#tag=v2.0'
```

### `vcsinstall dependency list` command

Dependency list MUST report every declared dependency, including dependencies without entries.
It MUST include repository identity, effective selector, and the matching shared locked revision, or null when no matching lock exists.
It MUST use Local state inspection in `specs/behavior/locking.md` and report the per-dependency entry counts defined under Dependency records below.
A file mapping or directory mapping MUST each count as one entry regardless of how many files it contains.
Dependencies without entries MUST report zero for every entry count.
It MUST NOT require network access, a VCS client, or repository-cache access.
Output MUST NOT report upstream freshness or a count of entries that can be updated.
An empty dependency collection MUST succeed without dependency records.
No positional arguments or command-specific options MUST be accepted.

### `vcsinstall dependency outdated` command

Dependency outdated MUST accept zero or more declared dependency names and MUST NOT accept command-specific options.
Without names, it MUST check every declared dependency, including dependencies without entries.
With names, it MUST check exactly those dependencies; repeated names MUST select each dependency once.
Unknown names MUST fail as invalid arguments before fetching sources.
The command MUST invoke the `dependency outdated` contract under Read-only operations in `specs/behavior/locking.md`.
A missing or mismatching dependency lock MUST produce an operational diagnostic identifying the dependency and providing guidance to run `install` or `dependency update NAME`.
An outdated result MUST identify a source-revision difference rather than promise that an update can succeed or count entries whose source content would change.

Success MUST emit only dependency records for outdated dependencies, showing their locked and current revisions.
Finding outdated dependencies MUST NOT make the command fail.
No outdated dependencies or an empty dependency selection MUST succeed without result records.

Examples:

```bash
vcsinstall dependency outdated
vcsinstall dependency outdated ci
vcsinstall dependency outdated ci formatting
```

### `vcsinstall dependency remove` command

Dependency remove MUST require exactly one declared dependency name.
Unknown names MUST fail as invalid arguments.
A refusal because the dependency still has entries MUST identify them and provide guidance to remove or reassign them first.
There MUST NOT be an implicit cascade or an option bypassing this check.

The command MUST invoke the `dependency remove` operation contract in `specs/behavior/locking.md`.
The command MUST operate locally without a VCS client or repository-cache access and MUST preserve cached repositories and remote contributions.

### `vcsinstall dependency update` command

Dependency update MUST accept zero or more declared dependency names and MUST NOT accept command-specific options or entry path selectors.
Without names, it MUST select every declared dependency, including dependencies without entries.
With names, it MUST select exactly those dependencies; repeated names MUST select each dependency once.
Unknown names MUST fail as invalid arguments before fetching sources or changing project files.
An empty dependency collection MUST succeed without result records.
The root MUST NOT expose an `update` command or alias.

The command MUST invoke the `dependency update` operation contract in `specs/behavior/locking.md` for the selected dependencies.

Examples:

```bash
vcsinstall dependency update
vcsinstall dependency update ci
vcsinstall dependency update ci formatting
```

### `vcsinstall dependency push` command

Dependency push MUST require exactly one declared dependency name followed by zero or more `ENTRY_PATH` arguments and the required `--mode MODE`.
An unknown name MUST fail as an invalid argument before source access or contribution effects.
The dependency name MUST NOT be inferred from entry paths or local changes.
The root MUST NOT expose a `push` command or alias.

#### Mode and options

`--mode` MUST select exactly one value under Delivery modes in `specs/behavior/vcs-interaction.md`.
An unsupported mode, hosting/source combination, or mode-specific option MUST fail as an invalid argument before reading command payloads or accessing sources and hosting services.

The command MUST accept these command-specific options:

- `--mode MODE` — Required delivery outcome.
- `--branch BRANCH` — Contribution branch name for PR and branch modes, or existing destination branch for direct mode.
- `--push-repository REPOSITORY` — Explicit publication repository for PR, branch, and direct modes, without changing the installation source.
- `--base BRANCH` — Review target branch for PR mode.
- `--hosting-provider PROVIDER` — Explicit hosting implementation for required acquisition and the selected delivery mode.
- `--output PATH` / `-o PATH` — Required patch artifact file in patch mode.
- `--message MESSAGE` / `-m MESSAGE` — Commit message for modes preparing a commit; repeated values MUST become separate paragraphs.
- `--file PATH` / `-F PATH` — UTF-8 commit-message file for modes preparing a commit, with `-` selecting standard input.

Options MUST be accepted only in the modes listed for them.
Message options MUST be accepted in all modes except overwrite.
`--message` and `--file` MUST be mutually exclusive.
Relative message-file paths MUST resolve against the invocation's working directory after home expansion.
Without either message option, modes preparing a commit MUST supply a nonempty message without launching an editor.
The resolved message MUST apply to that commit or patch metadata.

#### Entry selection

Without entry paths, the command MUST inspect only the named dependency's declared entries and select all locally changed entries belonging to it.
A known dependency with no changed entries MUST succeed without delivery effects or installation records once local selection and option validation passes, including when it has no entries.
With entry paths, it MUST select exactly the declared entries identified under the common entry-selection rules, including unchanged entries.
Every explicitly selected entry MUST belong to the named dependency.
Unknown entry paths and entries belonging to another dependency MUST fail as invalid arguments before fetching sources or invoking delivery effects.
This membership restriction MUST apply even when dependencies share a repository, requested selector, and locked revision.
Entries from different dependencies MUST NOT be combined in one invocation.
The command MAY select only some entries of the named dependency; verification and exclusion of unselected entries MUST follow the `dependency push` operation contract in `specs/behavior/locking.md`.

#### Preparation and delivery

The command MUST invoke the `dependency push` operation contract in `specs/behavior/locking.md` for baselines and change detection.
The command MUST process locally changed entries from the valid selection, including tracked additions and deletions.
Clean explicitly selected entries MUST succeed as unchanged once validation passes.

Contribution publication, Publication destination selection, and the selected mode contract in `specs/behavior/vcs-interaction.md` MUST govern effects.

When PR or branch mode delivers changed entries, omitting `--branch` MUST cause a contribution branch name to be chosen automatically and reported.
The option MUST NOT change dependency or source-version selection.

#### Hosting and publication selection

Hosting selection MUST follow Hosting selection and capabilities in `specs/behavior/vcs-interaction.md`.

`--push-repository` MUST select one repository interpreted through the named dependency's backend.
A VCS-qualified locator MUST agree with that backend; an unqualified native locator MUST use the already selected backend rather than infer another source kind.
Version fragments MUST NOT be accepted because the argument selects a recipient, not a source version.
Relative native filesystem locators MUST resolve against the invocation's working directory.

Direct-mode destination branch selection MUST follow Destination branch in `specs/behavior/hosting/git.md`.
PR review target selection MUST follow PR target selection in the selected hosting contract, including `specs/behavior/hosting/github.md` for GitHub.

#### Patch output

Patch mode MUST require a filesystem `--output` path; `-` MUST fail as an invalid argument rather than select standard output.
Home markers MUST expand, and relative paths MUST resolve against the invocation's working directory.
The path MUST name the complete artifact file, without project-path notation or an implicit filename suffix.
Standard output MUST remain reserved for the selected protocol's result and diagnostic cells.

The artifact path MUST NOT overlap:

- the selected project configuration.
- the project lockfile.
- any declared installation destination.
- tool-managed cache data.
- native source administrative data.

An existing target MUST be a regular file rather than a directory or symbolic link.
An explicitly selected unmanaged regular file MAY be replaced.
Artifact replacement MUST be atomically visible; preparation or replacement failure MUST preserve an existing artifact or leave an initially absent file absent.
The artifact MUST be written only after successful validation and patch preparation.
An empty changed selection MUST leave the output file unchanged.

#### Results

Success MUST emit one installation record per selected entry using `dependency push` as the operation.
Results MUST identify the delivery mode and the actual completed outcome under Installation records below.
Failures and completion guarantees MUST follow Failures and completed effects in `specs/behavior/vcs-interaction.md`.

Examples:

```bash
vcsinstall dependency push ci --mode pr
vcsinstall dependency push ci @/specs/shared --mode pr --branch contribute/specs -m "Clarify the shared specification"
vcsinstall dependency push formatting @/licenses/shared.txt @/docs/shared --mode branch -F ./contribution.txt
vcsinstall dependency push ci --mode branch --push-repository git+ssh://git@example.org/alice/shared.git
vcsinstall dependency push ci --mode direct -m "Clarify the shared specification"
vcsinstall dependency push ci-stable --mode direct --branch release/1.x
vcsinstall dependency push ci --mode patch --output ./ci-fix.patch
vcsinstall dependency push ci-stable --mode pr --base release/1.x --hosting-provider github
vcsinstall dependency push local-guides --mode overwrite
```

Each entry-path example requires all supplied entries to belong to the named dependency.

## `vcsinstall add` command

Add MUST require exactly one `SOURCE` URL and one `DESTINATION` path.
Missing or extra positional arguments MUST fail before fetching sources or changing project files.
It MUST create the corresponding entry declaration and install its content by default.
Only `--replace` and `--allow-outdated` MUST be accepted as command-specific options; repository and version-selection options MUST belong to `dependency add`.

Without `--replace`, add MUST fail if the supplied canonical destination is already declared, even when the requested mapping is identical.
The error MUST identify the conflicting destination and explain that intentional redefinition requires `--replace`.
This check MUST complete before fetching sources or changing project files.

Add MUST accept `--replace` to permit redefinition of the entry at the supplied destination, including assigning it to a different declared dependency.
For an already declared destination, it MUST replace the existing declaration rather than create a second entry.
For an undeclared destination, it MUST create a new entry normally, including when `--replace` is supplied.
`--replace` MUST authorize only entry redefinition; it MUST NOT bypass destination validation or Installation protection in `specs/behavior/locking.md`.

Add MUST invoke the `add` operation contract in `specs/behavior/locking.md`.
`--allow-outdated` MUST grant that contract's permission to install from an outdated matching lock.
`--replace` MUST NOT imply `--allow-outdated`.
A required freshness failure or an unpermitted revision mismatch MUST produce an operational failure.
The mismatch diagnostic MUST identify the dependency, its requested selector, and both revisions and suggest `dependency update NAME` or `--allow-outdated`.

Examples, using previously declared dependencies:

```bash
vcsinstall add dependency://ci/specs @/specs/shared
vcsinstall add dependency://formatting/LICENSE @/licenses/shared.txt
vcsinstall add dependency://formatting/guides @/docs/shared
vcsinstall add --replace dependency://formatting/specs @/specs/shared
vcsinstall add --allow-outdated dependency://ci/guides @/docs/shared
vcsinstall add --replace --allow-outdated dependency://formatting/specs @/specs/shared
```

## `vcsinstall install` command

Install MUST invoke the `install` operation contract in `specs/behavior/locking.md` for the selected configuration.
It MUST NOT accept positional arguments or source-selection options.
`--locked` MUST select locked installation under that contract.

Examples:

```bash
vcsinstall install
vcsinstall install --locked
```

## `vcsinstall remove` command

Remove MUST accept one or more `ENTRY_PATH` arguments selecting declared entry roots.
Bare `remove` MUST fail as a usage error, including when removal options are supplied without entry paths.
Selections MAY span source repositories and versions.
Unknown entries MUST fail as invalid arguments, and all selectors, existing project metadata, and applicable content checks MUST be validated before any files or project metadata are changed.

Both removal modes MUST invoke the `remove` operation contract in `specs/behavior/locking.md`.

### Removing installed content

Without `--keep-files`, remove MUST delete the selected destinations and their installed content.
For a directory entry, deletion MUST remain confined to that destination's tree.
Existing content that is locally modified or cannot be verified against a trustworthy locked baseline MUST cause an operational failure unless `--force` is supplied.
Verification MUST use Content identity and Local state inspection in `specs/behavior/locking.md`.

Already missing installed files or directories MUST NOT by themselves prevent removal of the declarations and associated local metadata.
Any remaining content scheduled for deletion MUST still satisfy the applicable verification rules.

`--force` MUST waive only local-content and baseline-verification refusals for selected entries.
It MUST NOT bypass destination validation or ignore failures while changing files or project metadata.

### Keeping installed content

`--keep-files` MUST remove management metadata while leaving the selected files and directories untouched, including local modifications and additions.
It MUST NOT require content verification or a clean installation baseline.
Retained content MUST become unmanaged project content.
`--keep-files` MUST take precedence over `--force`; supplying both MUST still preserve all installed content.

### Local effects and results

Both forms MUST operate without network access, a VCS client, or repository-cache access.
They MUST preserve shared cached repositories and previously published remote contributions.
Success MUST emit a removal record for each selected entry under the result contract below.

Examples:

```bash
vcsinstall remove @/specs/shared
vcsinstall remove @/licenses/shared.txt @/docs/shared
vcsinstall remove --keep-files @/specs/shared
vcsinstall remove --force @/docs/shared
```

## `vcsinstall list` command

List MUST accept optional `ENTRY_PATH` arguments under the common entry-selection rules and report every selected entry together with its locked revision and local status, including declared entries that are not currently installed.
Human and LLM output MUST show the owning dependency's declared name as an explicit label on each entry, for example `dependency: ci`.
Automation output MUST carry the same name in the installation record's `dependency` metadata field.
List MUST NOT emit separate dependency summary records or results for dependencies without selected entries.
The command MUST use Local state inspection in `specs/behavior/locking.md`.
An empty configuration MUST produce no installation records and exit successfully.

## `vcsinstall cache` command group

Bare `cache` MUST show the group's help without changing cache contents or requiring project files, a cache directory, or a VCS client.
The required subcommand MUST be `clean`.

### `vcsinstall cache clean` command

Cache clean MUST follow Cleanup in `specs/behavior/cache.md` for the effective cache root selected by the global `--cache-dir` rules.
It MUST NOT accept repository or entry path selectors, positional arguments, or command-specific options.
No confirmation prompt or `--force` option MUST be required.

Success MUST emit one cache result record, including for an absent or already empty cache.

Examples:

```bash
vcsinstall cache clean
vcsinstall --cache-dir ./temporary-cache cache clean
```

## `vcsinstall skill` command

`skill [DOCUMENT]` MUST follow the Skill command contract in `specs/behavior/cli.md` in `llm-tool-cli`.
The default and required document MUST be `usage`.
Additional document choices MAY be introduced when matching packaged documentation is available.
Document content MUST follow `specs/behavior/skill_fixtures.md`.

## `vcsinstall version` command

Version MUST report the installed version of distribution `vcsinstall` under the Version command contract in `specs/behavior/cli.md` in `llm-tool-cli`.

## Output behavior

Output MUST follow the cell formatting and delivery contracts in `specs/behavior/protocol.md` in `llm-tool-cli`.
The default stdout stream MUST be used for result, warning, and expected-error delivery in every protocol; help and unadapted framework argument-parsing diagnostics retain their shared behavior.
External VCS output MUST NOT be copied directly into the protocol stream; useful diagnostic information MUST be delivered through appropriate cells.
vcsinstall MUST NOT add terminal styling or progress-control sequences.

Payloads and ordering MUST be deterministic for the same inputs, selected project, and resolved local/source state, except for generated cell identifiers and automatically chosen contribution branch names.
Human output SHOULD present compact installation summaries.
LLM output SHOULD make source identity, destination, and outcomes explicit.
Consumers MUST NOT treat generated cell ids as installation identifiers.

Each command that selects, installs, or removes entries MUST emit one ordinary entry result per selected entry after success, ordered by canonical destination.
The following commands MUST emit one dependency record for each selected dependency, including those without entries:

- `dependency add`.
- `dependency list`.
- `dependency remove`.
- `dependency update`.
- `install`.

Dependency outdated MUST emit dependency records only for dependencies found to be outdated under its command rules.
Dependency list and dependency outdated MUST NOT emit installation records.
When both record types are required, dependency records MUST precede entry records; dependencies MUST be ordered by name and entries by canonical destination.

### Dependency records

The record kind and `type` metadata MUST be `dependency`.
Automation dependency records MUST expose:

- `operation` — The invoking command's name, using one of:

  - `dependency add`.
  - `dependency list`.
  - `dependency outdated`.
  - `dependency remove`.
  - `dependency update`.
  - `install`.

- `dependency` — The project-local dependency name.
- `vcs` — The source backend.
- `repository` — The normalized native repository locator, excluding its version fragment.
- `ref_kind` — The effective selector kind, using the installation record values below.
- `ref` — The effective selector value, or null when no named or pinned selector is available.
- `revision` — The matching shared locked revision, or null when no matching dependency lock exists.

Mutating dependency records MUST also expose `outcome`, using:

- `added`, `replaced`, or `unchanged` for `dependency add`.
- `removed` for `dependency remove`.
- `installed` or `unchanged` for `install`.
- `updated` or `unchanged` for `dependency update`.

For `install` and `dependency update`, a dependency MUST be reported as unchanged only when its shared lock, entry baselines, and installed content require no changes.
Records from `dependency update` or dependency redefinition MUST include `previous_revision`, using the prior recorded revision or null when no previous trustworthy lock existed.
Dependency removal records MUST describe the definition and matching revision observed before removal; they MUST NOT imply that the dependency remains declared.
Dependency list MUST NOT require a lockfile or fabricate a revision from a cached branch or tag.
Dependency and installation selector fields MUST use the explicitly configured selector or, for selector-less Git configuration with a matching lock, the recorded branch name with `ref_kind = branch`.
An omitted Git selector without a matching lock MUST use `ref_kind = default` and null `ref` without fetching source information.

Dependency list records MUST additionally expose these nonnegative integer counts:

- `entries` — The total number of declared entries belonging to the dependency.
- `modified` — The number of those entries with local status `modified`.
- `missing` — The number of those entries with local status `missing`.
- `unlocked` — The number of those entries with local status `unlocked`.

Each entry MUST contribute to exactly one category under Local state inspection in `specs/behavior/locking.md`.
The sum of `modified`, `missing`, and `unlocked` MUST NOT exceed `entries`; all remaining entries MUST be clean.
These counts MUST describe local state without implying that upstream freshness has been checked.

Dependency outdated records MUST additionally expose `current_revision`, containing the current immutable revision resolved under the dependency's effective selector.
The existing `revision` field MUST retain the matching locked revision; both values MUST be present and MUST differ for each reported dependency.

### Installation records

Results for entries that remain declared after the operation MUST use the installation record contract.
The record kind and `type` metadata MUST be `installation`.
Automation records MUST expose these common fields:

- `operation` — The invoking command's name, including the complete subcommand path for `dependency add`, `dependency update`, and `dependency push`.
- `dependency` — The name of the dependency owning the entry.
- `destination` — The canonical project-path identifier selecting the entry.
- `vcs` — The source backend name.
- `repository` — The normalized native repository locator, excluding the version fragment; `vcs` identifies its backend.
- `source_path` — The repository-relative source path.
- `ref_kind` — The effective source-selector kind, with these values:

  - `default`.
  - `branch`.
  - `tag`.
  - `revision`.

- `ref` — The owning dependency's effective selector value, or null when no named or pinned selector is available.
- `revision` — The dependency's shared locked immutable revision, or null when its lock or the entry baseline does not match.
- `status` — The status under Local state inspection in `specs/behavior/locking.md`, observed after the command's effects.

Installation records from mutating commands MUST also expose `outcome`, using:

- `installed` or `unchanged` for `add` and `install`.
- `updated` or `unchanged` for `dependency update` and `dependency add`.
- `pushed` for completed repository delivery, `exported` for completed patch delivery, or `unchanged` for unchanged entries in `dependency push`.

Entry records from `dependency update` or dependency redefinition MUST include `previous_revision`.
Repository and selector fields MUST describe the owning dependency rather than imply per-entry overrides.
Entry records for one dependency MUST agree on the shared revision when their baselines match.
Push records MUST additionally expose:

- `mode` — The explicitly selected delivery mode.
- `push_repository` — The actual recipient for a pushed entry, or null when no repository delivery completed for that entry.
- `branch` — The published contribution branch for PR or branch delivery, or existing target branch for direct delivery, otherwise null.
- `push_revision` — The resulting or verified recipient revision for completed repository delivery, otherwise null.
- `hosting_provider` — The selected hosting implementation for completed delivery of changed content, including patch export, otherwise null.
- `base_branch` — The actual review target branch for completed PR delivery, otherwise null.
- `review_url` — The confirmed hosted review URL for completed PR delivery, otherwise null.
- `patch_file` — The effective absolute artifact path for exported patch delivery, otherwise null.

Unchanged entries MUST use null for these effect fields other than `mode`, including when changed entries from the same selection were delivered.
Every changed entry delivered by one invocation MUST identify the same mode and applicable contribution metadata.
Direct delivery MUST use `pushed` for locally changed entries verified as already present at the destination, with that observed recipient revision, even when no new commit is required.
For filesystem overwrite, `push_repository` MUST identify the original source directory and `push_revision` MUST be the resulting complete source fingerprint.
For patch delivery, `patch_file` MUST identify the same complete artifact for each exported entry; repository and review effect fields MUST be null, while `hosting_provider` MUST identify the selected implementation.
The `revision` field MUST continue to identify the installation baseline rather than the newly published source revision.

### Removal records

Remove MUST use record kind and `type` metadata `removal`.
Automation removal records MUST expose:

- `operation` — `remove`.
- `dependency` — The name of the dependency to which the entry belonged.
- `destination` — The canonical project-path identifier of the removed entry.
- `outcome` — `removed`, identifying completed removal of the entry's declaration and tracking metadata even when its content was already absent.
- `keep_files` — A Boolean identifying whether the content-retention mode was requested, including when the destination was already absent.

Removal records MUST NOT require a post-removal installation status or an existing lock record.

### Cache records

Cache clean MUST use record kind and `type` metadata `cache`.
Automation cache records MUST expose:

- `operation` — `clean`.
- `cache_dir` — The effective absolute cache-root filesystem path.
- `outcome` — `cleaned` when disposable cached data was removed, or `unchanged` when none existed.

### Record delivery

The project-specific automation result records defined in this section MUST be metadata-only, with null `content` under the shared protocol contract.
Human and LLM projections MUST preserve the same semantic information and MAY add concise explanatory content.
Shared utility, warning, and error records MUST retain their owning library contracts.
Commands permitting an empty default selection MUST emit no entry result records and succeed for that selection; invalid explicit selectors MUST still fail.

## Failures and exit statuses

Explicit command-shape and selector validation MUST follow the Invalid-argument diagnostics contract in `specs/behavior/cli.md` in `llm-tool-cli`.
Command failures MUST follow that specification's Command error handling and Exit statuses contracts.
Configuration and path diagnostics MUST propagate without losing their native diagnostic information.

Unchanged results and listing local changes or outdated dependencies MUST count as success.
Local-change conflicts, VCS failures, and permission failures MUST use the shared expected-operational-failure category.
Error representation and unexpected technical failures MUST follow `specs/architecture/errors.md`; environment-error cells MUST follow `specs/behavior/protocol.md` in `llm-tool-cli`.

Command-shape, option-conflict, and local entry-selection failures MUST be detected before fetching sources or applying changes.
Failures requiring source-reference resolution MAY follow source fetching but MUST precede project changes or contribution publication.
Local-change and source-preparation failures MUST be detected before applying project changes or publishing contributions.
Failures after completed effects MUST follow Preparation and consistency in `specs/behavior/locking.md` for project changes, Failures and completed effects in `specs/behavior/vcs-interaction.md` for contributions, and Cleanup in `specs/behavior/cache.md` for cache cleanup.
