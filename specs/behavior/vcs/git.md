# Git sources

## Goal of the document

This document describes Git source identity, commit resolution, and extraction of committed content.

## Scope

This specification covers interpretation of available Git repository views, version selectors, immutable revisions, and committed content.
The following are out of scope:

- shared command parsing and persisted schemas.
- content-hash computation and installation-baseline transitions.
- cache layout and synchronization mechanisms.
- implementing Git protocols.
- repository acquisition, refresh, and contribution workflows.

## Backend and repository identity

The backend name MUST be `git`.
The common contract MUST follow `specs/behavior/vcs-interaction.md`.
Repository acquisition, refresh, and outgoing changes MUST follow `specs/behavior/hosting/git.md` or `specs/behavior/hosting/github.md` for the selected hosting implementation.
Repository locators MUST follow Repository locators and identity in `specs/behavior/cli.md`, including qualified URLs, native Git schemes, local paths, and scp-style addresses.
Configured locators MUST retain Git's native form under `specs/behavior/config.md`.

Client execution MUST follow External integrations in `specs/architecture/modules_layout.md`.
An unsupported client feature or repository object format MUST fail explicitly rather than select another backend or revision.
Inspection of cached views MUST follow `specs/behavior/cache.md`.
Source inspection MUST use supplied local Git data and reference metadata without clone, fetch, transport authentication, or remote-reference discovery.

A local Git source MUST provide committed repository content; local working-tree and index changes MUST NOT be included.
Source access MUST NOT modify the original local repository's:

- working tree.
- index.
- checked-out branch.
- configuration.

## Version selectors

### Branches and default selection

A branch selector MUST identify the exact named branch in the source repository's `refs/heads/` namespace.
Its name MUST be validated as a native Git branch name and MUST NOT be interpreted as a glob or revision expression.
Resolution requiring a current branch target MUST use observations refreshed through hosting under Source freshness in `specs/behavior/locking.md`, including remote rewinds and deletions.
A stale cached branch MUST NOT satisfy a failed refresh or hide deletion.

Default-version inference MUST use the source's advertised default-branch metadata supplied by hosting and record its actual branch name under Version selection in `specs/behavior/locking.md`.
It MUST NOT assume `main` or `master`, select the first advertised branch, or substitute the cache's checked-out branch.
An ambiguous or unavailable default branch, or one without a resolvable commit, MUST fail before project mutation.
Later operations MUST retain the inferred branch even if the remote's default changes.

### Tags

A tag selector MUST identify the exact named ref in `refs/tags/`.
Lightweight and annotated tags MUST be supported when they resolve to a commit; annotated tags MUST resolve through tag objects to that commit.
A tag resolving only to a tree or blob MUST fail as an unsupported source revision.
The recorded selector MUST remain the tag name, while the locked revision MUST be the resulting commit's identifier.

Resolution requiring a current tag target MUST use refreshed tag observations supplied through hosting rather than assume tags cannot move.
A moved or deleted tag MUST follow the ordinary freshness and failure contracts; installation reusing a valid lock MUST continue requesting its locked commit.
Tag movement MUST NOT rewrite an existing lock implicitly.

### Immutable revisions and ambiguity

An immutable revision selector MUST identify a commit by its complete hexadecimal object identifier or an unambiguous abbreviation accepted during CLI declaration.
The recorded pin and locked revision MUST use the full lowercase commit identifier in the repository's storage object format.
Git SHA-1 and SHA-256 object formats MUST be supported when the installed client supports the repository; a fixed 40-character assumption MUST NOT be imposed on every Git identifier.
Symbolic expressions such as `HEAD` or `main~2` MUST NOT be recorded as immutable pins.
Tree, blob, and tag-object identifiers MUST NOT be recorded in place of the selected commit identifier.

Bare references MUST be resolved under the CLI ambiguity rules rather than Git's implicit ref precedence.
In particular, a branch and tag sharing a name MUST remain ambiguous even when they resolve to the same commit.

The [Git revision documentation](https://git-scm.com/docs/git-rev-parse) describes commit verification and repository object formats; these formats MUST remain distinct from vcsinstall's installed-content hashes.

## Reading committed content

A requested commit MUST be read from the supplied repository view, with acquisition of missing objects governed by the hosting contract.
If required objects remain unavailable, including after cache cleanup, extraction MUST fail rather than use another commit or a working-tree copy.
Shallow or partial cached data MUST NOT make an incomplete tree appear complete.
Local object inspection MUST NOT trigger an implicit fetch of missing objects.
All entries prepared for one dependency operation MUST use its selected commit even if a branch or tag moves during acquisition.

The content root MUST be the committed tree, excluding Git administrative data belonging to the original repository or cache.
Source `.` MUST select that tree, and literal source paths MUST be interpreted relative to its root independently of a client's current directory.
Ordinary and executable Git blobs MUST extract as regular files, and trees MUST extract as directories.
Committed executable-file metadata SHOULD be preserved where the destination filesystem supports it; local permission-only changes MUST remain outside the content identity defined by `specs/behavior/locking.md`.

Extracted file bytes MUST be the committed blob bytes.
Extraction MUST NOT apply:

- end-of-line conversion.
- clean, smudge, or text-conversion filters.
- archive `export-ignore` exclusions.
- archive `export-subst` substitutions.

Gitignore rules MUST NOT exclude committed content selected for installation.
Stored pointer files MUST remain their stored bytes; fetching external payloads through an extension such as Git LFS MUST NOT occur implicitly.
Symbolic-link entries and submodule entries within selected content MUST fail explicitly before project mutation; they MUST NOT be followed, silently omitted, or expanded into another repository.
An unsupported object outside the selected extraction MUST NOT by itself prevent extracting an independent supported path.

**Note:** [Git object inspection](https://git-scm.com/docs/git-cat-file) distinguishes stored bytes from filtered output. [Git archive attributes](https://git-scm.com/docs/git-archive#_attributes) can omit or transform committed files, so an extraction method must account for those differences.
