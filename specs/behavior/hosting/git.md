# Git hosting

## Goal of the document

This document describes native Git repository acquisition and refresh, together with delivery of selected local changes.

## Scope

This specification covers repository acquisition, transport and reference observations, contribution preparation, and native delivery effects and failures.
The following are out of scope:

- source locator parsing, version selection, and extraction.
- hosted fork and review management.
- project declarations, installation-baseline transitions, and persisted formats.
- cache layout and access coordination.
- implementing Git protocols.

## Availability and repository access

The hosting implementation name MUST be `git`, compatible with source backend `git`.
It MUST support `branch`, `direct`, and `patch` modes.
PR and overwrite modes MUST fail as unsupported under the CLI's invalid-argument contract before payload reads or source access.
The common hosting contract MUST follow `specs/behavior/vcs-interaction.md`.
Client selection and integration MUST follow `specs/architecture/modules_layout.md`.

Source revisions and content MUST retain `specs/behavior/vcs/git.md` semantics.
Required Git object retrieval and preparation MUST follow `specs/behavior/cache.md`.
Source and publication repository identities MUST remain separate when their locators differ.
Publication MUST use the selected recipient's native transport and authentication without allowing configured push defaults to redirect it or include additional refs.

## Repository acquisition and refresh

Git's supported native transports and configured authentication facilities MUST retain their native meaning for both acquisition and delivery.
Acquisition MUST make required local Git objects and source metadata available under Repository acquisition and refresh in `specs/behavior/vcs-interaction.md`, reusing or rebuilding repository data under `specs/behavior/cache.md`.
Valid available objects MAY satisfy requests for immutable revisions without refreshing unrelated source references.

When freshness is required, observations MUST reflect the declared source's actual branch and tag targets, including moved or deleted refs.
Default-version discovery MUST provide the source's advertised symbolic `HEAD` and branch metadata without assuming `main` or `master`.
Version interpretation, selector ambiguity, commit verification, and extraction MUST follow `specs/behavior/vcs/git.md` using the supplied view.

**Note:** Git exposes advertised refs and symbolic `HEAD` through its [remote-reference inspection contract](https://git-scm.com/docs/git-ls-remote). The advertised default branch and its selected commit are separate values.

Acquisition from a local Git source MUST obtain committed repository data and preserve the original repository under Native client execution in `specs/behavior/vcs-interaction.md`.
It MUST NOT create source refs, include uncommitted content, or make an original source directory disposable cache data.
Read-only acquisition MUST NOT require a writable source or create a fork, contribution, or hosted review.

## Local publication safeguards

Publication to a local repository MUST preserve its:

- working tree.
- index.
- checked-out branch selection.
- configuration.

Authorized publication MAY create or advance refs only under the selected mode's rules and Git's normal receiving policies.
Direct delivery to a branch checked out in any worktree of a local recipient MUST fail before publication.
A local publication destination resolving to the target project's own Git repository MUST be rejected under the target-repository protection in General behavior in `specs/behavior/cli.md`.

## Change application and commit metadata

A prepared commit MUST reproduce selected file states and exact bytes while preserving unselected entries and their Git file modes from its parent tree.
Directory contribution scope and preservation of unselected content MUST follow Contributions and Filesystem effects in `specs/behavior/files-and-directories.md`.
Changed existing regular files MUST retain their executable-file mode from that tree.
New regular files MUST use the Git regular-file mode corresponding to their local executable status where the filesystem supports that distinction.
Permission-only local changes MUST NOT create a contribution.
Content that cannot be represented by Git MUST fail before delivery.
Preparation MUST NOT transform selected bytes through Git filters or include unrelated index or worktree changes.
Commit messages MUST follow `specs/behavior/cli.md`.

Authentication and commit-identity failures MUST remain explicit native-client failures.
Signing requirements and receiving-side policy failures MUST retain their native diagnostics.
vcsinstall MUST NOT invent an author identity, disable a receiving policy, or report a rejected push as publication.

## Contribution preparation

Branch and patch delivery MUST prepare one commit whose parent is the dependency's locked commit and whose tree applies only the selected source-path changes.
Native preparation for GitHub PR delivery MUST follow the same contract under `specs/behavior/hosting/github.md`.
An upstream branch advancing after installation MUST NOT cause an implicit rebase, merge, or change of that contribution's parent.
The source branch, source tag, and unrelated remote refs MUST remain unchanged.

## Branch delivery

Branch mode MUST publish the prepared commit to the source repository or the explicit publication repository selected under `specs/behavior/vcs-interaction.md`.
The contribution branch name MUST be valid for native Git and MUST follow the CLI's explicit or automatic branch-selection rules.

Publication MUST create exactly that new branch in the selected publication repository.
The absence requirement MUST remain enforced when the remote updates its ref, including when another writer creates the branch after an earlier availability check.
No existing branch MAY be advanced or replaced in branch mode, even if the proposed update would be a fast-forward.
A collision MUST cause an operational failure; verified PR retry under `specs/behavior/hosting/github.md` MUST NOT relax branch mode's absence requirement.

**Note:** Git's [conditional push contract](https://git-scm.com/docs/git-push) can require that a destination ref does not exist.

Success MUST identify the actual publication repository, new branch, and full commit identifier while retaining the installation revision under the CLI output contract.
Branch delivery MUST NOT create a fork or hosted review.
Permission failure MUST NOT trigger a different recipient or automatic fork creation.

## Direct delivery

### Destination branch

Direct mode MUST select one existing branch in the source repository or the explicit publication repository.
An explicit `--branch` MUST select a literal native Git branch name in that recipient.
Without it, the dependency's effective branch selector MUST supply the branch name, including an inferred branch retained in a matching lock.
A tag or revision selector MUST require an explicit `--branch`; neither a repository default nor a branch containing the pinned commit MAY be substituted.
Selection MUST NOT change the dependency's selector or installation lock.

The selected branch MUST be inspected at its current complete commit identifier, B.
A missing branch MUST fail rather than be created.
The locked source commit need not remain retrievable when matching, trustworthy entry baselines are available; direct preparation MUST use B and those baselines.

### Content protection

Each selected entry with a contribution MUST be compared with its destination content at B using Local comparison and Contributions in `specs/behavior/files-and-directories.md`, including the complete directory trees in that comparison scope:

- If the destination equals the selected local content, that entry MUST require no write.
- Otherwise, if the destination equals the recorded installation baseline, the selected local change MAY be applied.
- Otherwise, the selection MUST fail as a conflict before commit creation or publication, identifying the affected entry and destination revision.

Directory comparison MUST cover the selected entry's complete contribution scope under Contributions in `specs/behavior/files-and-directories.md`.
Different remote edits within a selected directory MUST conflict even if they affect different files from the local edits, including source-only additions since the baseline.
Source content outside selected mappings MUST be preserved; type obstructions MUST retain the shared preservation checks.
Selected mappings MUST retain the common overlap and representability checks.
These checks MUST complete for the whole selection before any destination write.
Direct mode MUST NOT merge or rebase differing content automatically.

### Commit and completion

When writes are required, direct delivery MUST prepare one commit with parent B under Change application and commit metadata above.
It MUST preserve all unselected content from B, including upstream changes made since installation.
Publication MUST advance only the selected branch and only if its ref still equals B.
A destination ref differing from B when the update is applied, including a moved, deleted, or recreated branch, MUST cause failure without overwriting that state.
The update MUST be a fast-forward from B; history rewriting and ref deletion MUST NOT be authorized by this mode.
A failed conditional update MUST NOT be silently retried against a different branch head.

If every selected entry with a contribution already equals its destination content within the contribution scope, direct delivery MUST succeed without creating a commit or updating a ref.
That verified result MUST identify the observed B as its delivery revision.
Completed direct delivery, including this already-present case, MUST follow Installation records in `specs/behavior/cli.md`.

Direct delivery MUST NOT create a fork or hosted review or fall back to another recipient on permission failure.

## Patch delivery

Patch mode MUST export the prepared contribution as one Git `format-patch` mailbox message.
The artifact MUST include the commit message and author information, selected file changes, and the locked base commit information.
It MUST support binary changes and complete blob identifiers, preserving the selected contribution's applicable content and file modes.
The format MUST be suitable for native Git patch application, including `git am`, without a vcsinstall-specific patch format.

The artifact MUST follow Patch artifacts in `specs/behavior/vcs-interaction.md` and the CLI's `--output` contract.
Generating it MUST NOT send the artifact to another person.
Unavailable locked source content MUST remain a retrieval failure rather than be reconstructed from installed files.

The [Git format-patch contract](https://git-scm.com/docs/git-format-patch) defines the mailbox format, binary support, complete object identifiers, and base-commit information used here.

## Example

A dependency tracks `main` and has installed a shared guide at commit A.
The local guide is edited while upstream advances `main` to B by changing an unrelated file.

1. `vcsinstall dependency push guides --mode direct` verifies that the guide at B still matches its baseline at A.
2. It creates commit C with parent B, containing the edited guide and the unrelated upstream changes, then advances `main` only if it still points to B.
3. A retry before updating the installation finds the guide already equal at C and completes without another commit.
4. `vcsinstall dependency update guides` records C and the guide's matching content as the new installation baseline.

If B changes the guide differently, direct delivery fails before publication.
For a dependency pinned to a tag, the user must additionally select an existing destination branch, for example `--branch release/1.x`.
