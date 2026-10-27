# GitHub hosting

## Goal of the document

This document describes Git acquisition through GitHub hosting and GitHub contribution delivery, including forks, pull requests, and safe retries.

## Scope

This specification covers delegated repository acquisition and refresh, GitHub repository identity, and contribution delivery and recovery.
The following are out of scope:

- source resolution and extraction.
- native Git contribution and patch mechanics.
- project declarations, installation baselines, and persisted formats.
- other hosting services and automatic merging.

## Availability

The hosting implementation name MUST be `github`, compatible with source backend `git`.
The common hosting contract MUST follow `specs/behavior/vcs-interaction.md`.
Architectural composition and client selection MUST follow `specs/architecture/modules_layout.md`.

GitHub hosting MUST support repository acquisition and refresh as well as PR delivery and native Git modes.
Acquisition and native delivery MUST follow `specs/behavior/hosting/git.md`, including their service-client independence under `specs/behavior/vcs-interaction.md`.
Overwrite mode MUST fail as unsupported under the CLI contract.

## Repository acquisition and refresh

Repository data and reference observations MUST follow the native Git acquisition contract, including its cache and source-preservation guarantees.
Acquisition MUST preserve the declared source identity and native locator without substituting a fork-network root or another upstream repository.
Reading repository data MUST NOT require PR mode, writable-source discovery, fork creation, or review operations.

## PR service identity

GitHub.com repositories MUST support adapter inference from their source host.
Explicit adapter selection and unambiguous native-client configuration MUST support configured GitHub hosts, including enterprise hosts and resolved SSH aliases.
PR delivery MUST identify the exact declared source repository, host, and configured account before hosting mutations.
It MUST NOT substitute the root of a fork network or a client's default upstream repository for the declared source.
An ambiguous identity, unavailable required client, or unavailable authentication MUST cause an operational failure with actionable guidance.

## PR target selection

PR mode MUST use the source repository as the review target repository.
Without `--base`, an effective branch selector MUST supply the target branch, including an inferred branch retained in a matching lock.
A tag or revision pin MUST require an explicit `--base` rather than infer a branch from the tag, commit, or repository default.
An explicit base MUST select a literal existing branch in the source repository and MUST NOT alter the dependency's requested selector.

Before publication, the selected target branch MUST be inspected at its current source revision.
The dependency's locked commit MUST be an ancestor of or equal to that observed target revision.
Required ancestry information MUST be retrieved before this decision; incomplete cached history MUST NOT be treated as proof of an incompatible target.
An incompatible history MUST fail before fork creation or branch publication, identifying the locked commit and target branch and revision.
The adapter MUST NOT implicitly rebase or port changes to satisfy an incompatible target.
Target inspection MUST NOT advance the installation lock or change the prepared contribution's parent.

The review MUST use the contribution repository and branch as its head and the selected source branch as its base.
Neither client defaults nor the publication repository's default branch MAY override this selection.
[GitHub's PR API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request) separates head and base selection and requires an existing base branch.

## Publication repository and forks

With `--push-repository`, PR mode MUST validate that the selected repository is an eligible contribution head for the source on the same GitHub service.
Eligibility MUST be established through hosting metadata rather than a matching repository basename.

Without an override, the adapter MUST use the source repository when the authenticated account has source write access.
When that access is absent, it MUST discover and reuse an eligible writable fork owned by the configured account, or create one where the host's policy permits.
Mode `pr` MUST authorize that fork creation as part of the requested submission without a separate confirmation.
Fork selection MUST be unambiguous; an unresolved choice MUST fail rather than choose an unrelated repository.
A forbidden fork or unavailable writable destination MUST produce a diagnostic identifying the source and explaining how to supply an eligible publication repository.

A fork's default-branch-only state MUST NOT change the source revision or review target used for the contribution.
Required contribution objects MUST come from the original source preparation even if the fork does not yet contain the selected branch or commit.
A newly created fork MUST be available for the intended Git publication before the branch is pushed.

The [GitHub fork contract](https://docs.github.com/en/pull-requests/reference/forks) defines fork relationships and policy restrictions.
The [GitHub CLI](https://cli.github.com/manual/gh_pr_create) can offer its own fork and push choices; those choices MUST NOT override vcsinstall's resolved source, publication repository, head, or base.
Required hosted operations that exceed a convenience command's capabilities MUST use the selected client's authenticated API facilities or fail explicitly.

## Review creation, retry, and completion

After a contribution branch is available, PR mode MUST create or recover the matching review in the original source repository.
Its title and body MUST be noninteractive and derived from the resolved contribution message and source-path changes.
An implementation MUST NOT start an editor, interactive fork picker, or merge operation.

A retry specifying an existing contribution branch MAY reuse its commit only when its parent, complete tree, and commit message match the requested contribution.
Native branch publication MUST retain the conditional absence protection in `specs/behavior/hosting/git.md` unless this verified PR-retry exception applies.
A conflicting existing branch MUST fail without being advanced, replaced, or deleted.
This content verification MUST NOT require the cache to retain a previous publication record.

The adapter MUST look for an existing open review with the same source repository, publication repository, head branch and commit, and target branch before creating another.
A matching open review MUST be returned rather than duplicated.
An ambiguous or conflicting review MUST fail explicitly.
A matching closed or merged review MUST be reported with its state and guidance rather than reopened or duplicated automatically.

Completed PR delivery of changed content MUST confirm the actual contribution head and target under the completion contract in Failures and completed effects in `specs/behavior/vcs-interaction.md`.
Review failures and uncertain outcomes MUST retain that contract's retry information and completed-effect guarantees.
Recovery MUST use verified repository and review state rather than contribution records in project configuration or the lockfile.

## Examples

For a dependency tracking `release/1.x`, this command submits a contribution to that source branch, creating or reusing a fork when needed:

```bash
vcsinstall dependency push ci --mode pr --branch contribute/shared-guide -m "Clarify the shared guide"
```

A dependency pinned to `v1.0` requires an explicit target branch:

```bash
vcsinstall dependency push ci-stable --mode pr --base release/1.x
```

A retry can use the reported contribution branch without depending on prior cache contents:

```bash
vcsinstall dependency push ci --mode pr --branch contribute/shared-guide -m "Clarify the shared guide"
```
