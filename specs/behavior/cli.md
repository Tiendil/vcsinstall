# Command-line interface

## Goal of the document

This document describes vcsinstall's complete command-line interface, including inputs, results, and observable command effects.

## Scope

This specification covers command invocation, output, and observable effects at the CLI boundary.
The following are out of scope:

- configuration and lockfile schemas.
- revision locking, content hashing, and baseline state transitions.
- internal cache lifecycle and checkout organization.
- VCS adapter architecture.
- hosting-provider APIs and pull-request creation.
- exact skill prose.

## Dictionary

- `source backend` — the source kind responsible for interpreting a locator, initially Git.
- `native repository locator` — the repository address or filesystem path understood by the selected VCS client, without vcsinstall's version fragment.
- `NAME` — a project-local dependency name.
- `REPOSITORY` — a locator identifying a backend and native repository locator, optionally carrying a version fragment for `dependency add`.
- `SOURCE` — a `dependency://NAME/PATH` URL selecting a repository path within a declared dependency.
- `SOURCE_PATH` — a file or directory path relative to the source repository root.
- `DESTINATION` — the final project path at which `add` installs an entry.
- `ENTRY_PATH` — a local path selecting a declared entry by its destination, including when its installed content is missing.
- `REF` — an untyped source-version reference accepted in a repository URL fragment.
- `REVISION` — an immutable VCS revision identifier; Git revisions in results MUST be full commit hashes.

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
- `dependency add [OPTIONS] NAME REPOSITORY` — Declare and lock a named dependency; redefining one requires `--replace` and refreshes all its entries together.
- `dependency list` — List declared dependencies, their shared locked revisions, and local entry statistics.
- `dependency outdated [NAME...]` — Check all or named dependencies for changes in their configured source versions and report differing locked and current revisions.
- `dependency remove NAME` — Remove an unused dependency declaration and its lock.
- `dependency update [NAME...]` — Advance all or named dependencies within their configured version selectors and refresh each dependency's entries together.
- `dependency push [OPTIONS] NAME [ENTRY_PATH...]` — Publish changes from all or selected entries of one named dependency.
- `add [OPTIONS] SOURCE DESTINATION` — Declare and install one entry from a dependency's locked revision; outdated revisions require `--allow-outdated`, and redefining an existing destination requires `--replace`.
- `install [OPTIONS]` — Install the entries declared by the project, preferring their locked revisions.
- `remove [OPTIONS] ENTRY_PATH...` — Remove declared entries and their installed content, with `--keep-files` retaining the content as unmanaged project files.
- `list [ENTRY_PATH...]` — Inspect declared installations and their local changes.
- `cache clean` — Clear disposable data from the selected shared cache.
- `skill [DOCUMENT]` — Print built-in documentation.
- `version` — Print the installed tool version.

Locking operations and their state changes MUST follow Operation contracts and Preparation and consistency in `specs/behavior/locking.md`.

## Application identity and project files

LLM cell framing MUST use `VCSINSTALL`, including shared errors produced during argument validation.
The default project configuration filename MUST be `vcsinstall.toml`.
Selected configurations MUST follow the schema and validation requirements in `specs/behavior/config.md`.
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

Configuration discovery and project-root selection MUST follow the Existing configuration selection and Project scopes contracts in `specs/behavior/config.md` in `llm-tool-cli`, using the default filename above.
The selected configuration file's parent MUST be the project root.
Missing configuration MUST fail with the shared diagnostic rather than implicitly initialize a project.

The following interfaces MUST NOT load or create project files or require a cache or a VCS client:

- root and command help.
- shell completion.
- `version`.
- `skill`.

`cache clean` MUST NOT load or create project files or require a VCS client.
It MUST remain usable outside a project and with an unusable `--config` value.

## Global options

Global options MUST precede the subcommand.

### `--protocol PROTOCOL` / `-p PROTOCOL`

The protocol option MUST follow the Protocol option and Protocol selection contracts in `specs/behavior/cli.md` in `llm-tool-cli`.
The supported protocols MUST be `human`, `llm`, and `automation`.
An explicit protocol MUST override the command default.
Without an explicit protocol, `skill` MUST use LLM output and all other commands MUST use human output.

### `--config PATH`

Parsing MUST follow the Configuration option contract in `specs/behavior/cli.md` in `llm-tool-cli`, deferring filesystem validation until the configuration is used.
An explicit path MUST override configuration discovery without fallback to another project.
Configuration-independent commands MUST remain usable with an unusable `--config` value.

### `--cache-dir DIRECTORY`

This option MUST override the vcsinstall cache root for the invocation without changing project configuration or the lockfile.
The supplied directory MUST be the complete application cache root; vcsinstall MUST NOT append another `vcsinstall` component to it.
Home markers MUST be expanded, and relative directories MUST resolve against the invocation's working directory rather than the project root.

Without this option, the cache root MUST use the platform's per-user cache location followed by `vcsinstall`.
When the platform provides no usable default cache location, the fallback MUST be `<user-home>/.cache/vcsinstall`.
If no default or home-based fallback can be resolved, operations requiring a cache MUST fail with guidance to provide `--cache-dir`.

Cached repositories MUST reside under `<cache-root>/repos/<vcs-name>/<repo-name>`.
The VCS component MUST identify the source backend, such as `git`, rather than the complete transport scheme, such as `git+https`.
Repository cache identity MUST consist of the backend and normalized native repository locator defined below.
The repository-name component MUST be derived deterministically from that complete identity and MUST distinguish repositories sharing a basename.
It MAY combine a readable name with a digest of the normalized locator.

Dependency names and version selectors MUST NOT contribute to repository cache identity.
Source paths and local destinations MUST NOT create separate repository cache identities.
Dependencies using different branches, tags, or revisions of the same normalized repository MUST share its repository cache entry while retaining independent locks.
Different installed versions of one repository MUST remain independently usable through the same cache root.
Any cached checkouts or snapshots for specific versions MUST remain distinguishable by their immutable revisions; using one MUST NOT cause another entry to read the wrong revision.
Operations preparing source content MUST reuse valid cached repository data while obtaining the source freshness required by the command.
Use of cached source data for locking MUST follow Source integrity and cache independence in `specs/behavior/locking.md`.

Equivalent qualified and explicitly selected native locators MUST use the same cache identity.
For example, dependencies declared with `git+https://example.com/shared.git#branch=main` and `git+https://example.com/shared.git#tag=v1.0` MUST select the same repository cache entry.
Different native transports MUST remain separate identities; HTTPS and SSH locators MUST NOT be merged merely because they appear to address the same repository.
The backend namespace MUST also separate an ordinary HTTP source from a Git source with the same underlying HTTPS address if HTTP source support is introduced.

Cache-path validation and creation MUST occur only when an operation needs the cache.
An explicit unusable cache path MUST fail without falling back to the default.
Help and completion MUST ignore unavailable cache directories.
The following commands MUST also remain usable without a cache:

- `init`.
- `list`.
- `remove`.
- `dependency list`.
- `dependency remove`.
- `skill`.
- `version`.

## Dependency definitions and source arguments

### Named dependencies

Dependency names and declarations MUST follow `specs/behavior/config.md`, and terminology MUST follow `specs/dictionary.md`.
Shared revision and baseline semantics MUST follow State and shared revisions in `specs/behavior/locking.md`.

### Repository locators and identity

`REPOSITORY` MUST accept VCS-qualified URLs of the form `<vcs>+<transport>://...`, including `git+https`, `git+ssh`, and `git+file` for Git.
The VCS prefix MUST select the backend, and the remainder MUST identify the native repository locator using that backend's transport semantics.
Native schemes that identify a VCS, such as `git://`, MUST select that VCS when supported.
An explicit `--vcs` on `dependency add` MUST select the VCS for an unqualified native locator or agree with a VCS already identified by the scheme; a conflict MUST fail as an invalid argument.
Without an explicit VCS, unqualified local paths and scp-style repository addresses MUST default to Git.
Other transport-only URL schemes, such as `ssh://`, MUST require an explicit `--vcs` on `dependency add` or a VCS-qualified scheme.

Without an explicit `--vcs`, plain `file://`, `http://`, and `https://` locators MUST identify ordinary filesystem or HTTP sources rather than imply Git.
Their backend support MAY be introduced separately; until supported, these inputs MUST fail explicitly with guidance to use a VCS-qualified URL when a VCS repository was intended.
A `.git` suffix or a familiar hosting domain MUST NOT override this distinction.
For example, `git+file:///home/user/shared` MUST identify a Git repository, while plain `file:///home/user/shared` MUST identify ordinary filesystem content.

Client invocations MUST use locator syntax supported by the selected installed client and preserve its native transport meaning.
For example, Git's `git+ssh://...` MUST normalize to native `ssh://...`, while an SVN `svn+ssh://...` locator MUST retain the native SVN-over-SSH scheme when passed to its client.
vcsinstall's version fragment MUST NOT become part of the repository address passed to the client.

Relative native repository paths MUST resolve against the invocation's working directory; the recorded location MUST retain that meaning on later invocations from other directories.
Normalization MUST preserve the locator's native meaning, including transport and authority.
Repository identity MUST be the backend together with that normalized native locator, independent of URL selector spelling or equivalent option syntax.
Different locators MUST NOT be equated merely through matching basenames or assumed equivalence between HTTPS and SSH addresses.
Native query parameters MUST retain their transport meaning; vcsinstall MUST NOT interpret them as source-version selectors.
`@` in a transport address MUST NOT be interpreted as a vcsinstall version suffix.

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

Git MUST be the first supported VCS.
Other VCS choices, transports, or selector kinds MUST be accepted only when their behavior is supported; unknown or unsupported selections MUST fail explicitly without falling back to Git.
A required but unavailable VCS executable MUST produce an operational failure rather than trigger software installation.
Authentication and commit identity MUST use the selected client's configured facilities; failures MUST remain visible through the selected output protocol.

Additional VCS support MUST preserve native source semantics rather than impose Git's model on every backend.
For example, Mercurial may use `hg+https://...#tag=v1.0` or `hg+file:///...#rev=CHANGESET_HASH`, while SVN may use `svn+https://...#rev=1234`.
SVN branches and tags MUST be selected through explicit source paths without assuming a `trunk`, `branches`, or `tags` layout or inventing a native named-selector facility.
Backend-specific selector kinds, such as Mercurial bookmarks, MUST have explicit selection and result semantics before being exposed.

Plain filesystem and HTTP source notation MUST NOT imply VCS revision history or branch-based push support.
Support for those backends MUST define applicable operations and content-integrity behavior before being advertised; unsupported operations MUST fail explicitly.
The VCS installation and push contracts in this document MUST NOT be silently weakened to accommodate unversioned sources.

### Source paths

`SOURCE` MUST use `dependency://NAME/PATH`, resolving `NAME` only against declared project dependencies.
Unknown names MUST fail before fetching sources or changing project files.
The authority MUST contain only the exact dependency name, without user information or a port.
Query parameters and fragments MUST NOT be accepted.
Raw repository URLs and per-entry version options MUST NOT be accepted by `add`.
The path after the authority separator MUST be percent-decoded exactly once as `SOURCE_PATH`, preserving literal `+` characters.
An empty path, as in `dependency://ci/`, MUST select the repository content root, equivalent to `.`.
Malformed escapes and decoded paths escaping the repository root MUST fail explicitly.

`SOURCE_PATH` MUST name a literal repository-relative path at the selected revision, using `/` separators.
It MUST NOT imply hosting-specific package discovery or modify the dependency's repository locator or version selector.
It MUST NOT be interpreted as a package name or expanded as a glob.
`.` MUST select the repository's content root.
Absolute source paths and paths escaping that root MUST be rejected.
Source paths MUST NOT undergo local home expansion or interpretation relative to the invocation's working directory.

## Local path arguments and entry selection

`DESTINATION` and `ENTRY_PATH` arguments MUST follow the Mixed path normalization contract in `specs/behavior/paths.md` in `llm-tool-cli`, using the selected project root and the invocation's working directory as the base for relative filesystem inputs.
They MUST accept canonical `@/` identifiers and contained filesystem paths, including absolute paths and paths whose targets do not yet exist.
Shared path diagnostics MUST retain their native information.

The canonical destination path MUST identify an entry at the CLI boundary.
`DESTINATION` MUST identify the exact final file or directory path; an existing directory MUST NOT cause an implicit source-basename suffix to be appended.
A directory mapping MUST place its selected content directly under that destination.
Add MUST allow files and directories to be installed with names and paths different from those in the source repository.
Overlaps between distinct entries MUST fail before project mutation.
Destinations conflicting with the selected project configuration or lockfile MUST be rejected.

For `list`, omitted entry paths MUST select every declared entry.
Remove MUST require at least one explicit `ENTRY_PATH` and MUST NOT infer a selection when none is supplied.
`dependency push` MUST restrict entry-path selection to its named dependency under the rules below.
Explicit entry paths MUST select exactly the matching declared entry roots by their canonical destination paths, without implicit descendant selection or glob expansion.
Selecting an entry MUST NOT require its installed content to exist.
Inputs normalizing to the same entry MUST select it once.
Every explicit entry path MUST be validated before fetching sources, changing project files, or publishing changes.
An unknown entry path MUST fail instead of silently broadening or ignoring the selection.
The standard `--` argument separator MUST allow positional values beginning with `-`.

Entry result records MUST be ordered by canonical destination path.

## `vcsinstall init` command

Init MUST follow the Initialization command contract in `specs/behavior/cli.md` in `llm-tool-cli`, using invocation cwd, optional `--config`, and default filename `vcsinstall.toml`.
It MUST create a valid starter configuration with no dependencies or installed entries.
Starter contents MUST follow the Starter configuration contract in `specs/behavior/config.md`.
It MUST NOT fetch repositories or create a lockfile.
No positional arguments or command-specific options MUST be accepted.

Existing targets and failed initialization MUST follow the shared configuration-creation and initialization contracts without overwriting existing configuration.
Success MUST emit the shared creation acknowledgement identifying the created configuration path.

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

Success MUST emit one dependency record followed by installation records for any affected entries.

Examples:

```bash
vcsinstall dependency add ci 'git+https://example.com/team/shared.git#branch=main'
vcsinstall dependency add formatting 'git+https://example.com/team/shared.git#tag=v1.0'
vcsinstall dependency add --vcs git --branch main ci-native https://example.com/team/shared.git
vcsinstall dependency add --replace formatting 'git+https://example.com/team/shared.git#tag=v2.0'
```

### `vcsinstall dependency list` command

Dependency list MUST report every declared dependency, including dependencies without entries, ordered by name.
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

Success MUST emit only dependency records for outdated dependencies, ordered by name, showing their locked and current revisions.
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
Success MUST emit one dependency record describing the removed definition.

### `vcsinstall dependency update` command

Dependency update MUST accept zero or more declared dependency names and MUST NOT accept command-specific options or entry path selectors.
Without names, it MUST select every declared dependency, including dependencies without entries.
With names, it MUST select exactly those dependencies; repeated names MUST select each dependency once.
Unknown names MUST fail as invalid arguments before fetching sources or changing project files.
An empty dependency collection MUST succeed without result records.
The root MUST NOT expose an `update` command or alias.

The command MUST invoke the `dependency update` operation contract in `specs/behavior/locking.md` for the selected dependencies.
Success MUST emit one dependency record for each selected dependency followed by installation records for their entries, using `dependency update` as the operation.

Examples:

```bash
vcsinstall dependency update
vcsinstall dependency update ci
vcsinstall dependency update ci formatting
```

### `vcsinstall dependency push` command

Dependency push MUST require exactly one declared dependency name followed by zero or more `ENTRY_PATH` arguments.
An unknown name MUST fail as an invalid argument before fetching sources or publishing changes.
The dependency name MUST NOT be inferred from entry paths or local changes.
The root MUST NOT expose a `push` command or alias.

#### Entry selection

Without entry paths, the command MUST inspect only the named dependency's declared entries and select all locally changed entries belonging to it.
A known dependency with no changed entries MUST succeed without publication or installation records in this mode, including when it has no entries.
With entry paths, it MUST select exactly the declared entries identified under the common entry-selection rules, including unchanged entries.
Every explicitly selected entry MUST belong to the named dependency.
Unknown entry paths and entries belonging to another dependency MUST fail as invalid arguments before fetching sources, creating commits, or publishing changes.
This membership restriction MUST apply even when dependencies share a repository, requested selector, and locked revision.
Entries from different dependencies MUST NOT be combined in one invocation.
The command MAY select only some entries of the named dependency; unselected entries MUST NOT contribute local changes or require local-content verification.

#### Publication validation and commit creation

The command MUST invoke the `dependency push` operation contract in `specs/behavior/locking.md` for publication baselines and change detection.
Publication validation MUST complete before fetching sources, creating commits, or publishing changes.

The command MUST process locally changed entries from the valid selection, including tracked additions and deletions.
Clean explicitly selected entries MUST succeed as unchanged once publication validation passes.
A selection with no changed content MUST NOT create empty commits or branches.

Changes MUST be applied at the original source paths and committed together in one contribution branch based on the locked source revision.
Conflicting selected edits to the same source paths MUST fail before creating or publishing a contribution.

The command MUST accept only these command-specific options:

- `--branch BRANCH` — Choose the contribution branch name.
- `--message MESSAGE` / `-m MESSAGE` — Supply a commit message; repeated values MUST become separate paragraphs.
- `--file PATH` / `-F PATH` — Read the commit message from a UTF-8 file, with `-` selecting standard input.

Permission failures MUST fail explicitly without attempting publication to another repository.
The command MUST NOT assume that pushing implies permission to merge into the tracked source.

Without `--branch`, a contribution branch name MUST be chosen automatically and reported.
`--branch` MUST name the new contribution branch and MUST NOT affect dependency or source-version selection.
An existing remote branch MUST NOT be overwritten; a branch-name collision MUST cause an operational failure before publication.

`--message` and `--file` MUST be mutually exclusive.
Relative message-file paths MUST resolve against the invocation's working directory after home expansion.
Without either message option, vcsinstall MUST supply a nonempty message without launching an editor.
The supplied message MUST apply to the contribution commit.

Success MUST emit one installation record per selected entry using `dependency push` as the operation.
When changes are published, the results MUST identify:

- the publication repository.
- the contribution branch.
- the pushed revision.
- the affected destinations.

Success MUST report publication without implying acceptance into the tracked source.
Hosted pull-request creation MUST NOT be required for this command to succeed.

Examples:

```bash
vcsinstall dependency push ci
vcsinstall dependency push ci @/specs/shared --branch contribute/specs -m "Clarify the shared specification"
vcsinstall dependency push formatting @/licenses/shared.txt @/docs/shared -F ./contribution.txt
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
Success MUST emit dependency records followed by installation records for the selected configuration.

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

Cache clean MUST remove all disposable vcsinstall data from the effective cache root selected by the global `--cache-dir` rules.
This MUST include all cached repositories, across VCS types and cached versions, rather than only repositories referenced by the current project.
It MUST NOT accept repository or entry path selectors, positional arguments, or command-specific options.
No confirmation prompt or `--force` option MUST be required.

Cleanup MUST operate locally without network access or a VCS client.
It MUST preserve:

- installed content.
- project configuration.
- lockfiles.

Previously published remote contributions and original source repositories MUST remain unchanged.
Later source operations MUST follow Source integrity and cache independence in `specs/behavior/locking.md`.

An absent or already empty cache MUST succeed as unchanged without creating it.
The cache root and coordination metadata MAY remain after cleanup, but disposable cached data MUST be removed on success.
Only vcsinstall-owned disposable cache data MAY be deleted; unrelated content in a directory selected by `--cache-dir` MUST be preserved.
Cleanup MUST NOT follow symlinks within cached data to delete content outside the effective cache root.
If another vcsinstall invocation is using the cache, cleanup MUST fail with an operational diagnostic before deleting cached data.
Other cache-access or deletion failures MUST fail explicitly rather than report complete cleanup or fall back to another cache root.

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
The command MUST NOT load project files or use a repository cache or VCS client.

## `vcsinstall version` command

Version MUST report the installed version of distribution `vcsinstall` under the Version command contract in `specs/behavior/cli.md` in `llm-tool-cli`.
It MUST NOT load project files or use a repository cache or VCS client.

## Output behavior

Output MUST follow the cell formatting and delivery contracts in `specs/behavior/protocol.md` in `llm-tool-cli`.
All result, warning, and expected-error cells MUST use stdout in every protocol.
Automation stdout MUST contain only JSON Lines; help and unadapted framework argument-parsing diagnostics retain their shared behavior.
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
- `vcs` — The source VCS name.
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
- `pushed` or `unchanged` for `dependency push`.

Entry records from `dependency update` or dependency redefinition MUST include `previous_revision`.
Repository and selector fields MUST describe the owning dependency rather than imply per-entry overrides.
Entry records for one dependency MUST agree on the shared revision when their baselines match.
Push records MUST include `push_repository` identifying the named dependency's publication repository, including for unchanged entries.
Every push record from one invocation MUST identify the same publication repository.
Push records MUST also include `branch` and `push_revision` describing publication by this invocation, using nulls for unchanged entries.
Every pushed entry in an invocation MUST identify the same contribution branch and pushed revision.
The `revision` field MUST continue to identify the installation baseline rather than the newly pushed commit.

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

The default statuses MUST remain:

- `0` — Successful execution, including unchanged results and listing local changes or outdated dependencies.
- `1` — Explicit invalid-argument diagnostics.
- `2` — Configuration errors and unadapted framework parsing errors.
- `3` — Other expected operational failures, including local-change conflicts, VCS failures, and permission failures.

Multiple errors MUST retain their diagnostic order and use the highest declared exit status.
Shared diagnostics MUST retain their declared exit statuses.
Environment-error cells MUST retain native codes and context under `specs/behavior/protocol.md` in `llm-tool-cli`.
Unexpected technical failures MUST remain distinct from expected operational failures.

Command-shape, option-conflict, and local entry-selection failures MUST be detected before fetching sources or applying changes.
Failures requiring source-reference resolution MAY follow source fetching but MUST precede project changes or contribution publication.
Local-change and source-preparation failures MUST be detected before applying project changes or publishing contributions.
If an expected failure occurs after local or remote effects have completed, diagnostics MUST identify those effects and provide enough context for recovery or retry.
A failed `dependency push` MUST NOT imply that a contribution already published in that invocation was rolled back.
Failure MUST suppress pending ordinary success output and deliver errors followed by warnings under the shared delivery contract.
