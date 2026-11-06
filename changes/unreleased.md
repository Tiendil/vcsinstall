### Changes

- Added `init`, project discovery, and configuration and revision tracking in `vcsinstall.toml` and `vcsinstall.lock`.
- Added support for Git and filesystem dependencies, including Git branch, tag, and revision selection.
- Added dependency declaration, inspection, freshness checks, updates, and removal.
- Added file and directory installation through `add` and `install`, with local-change protection and `install --locked` to preserve existing lock state.
- Added `list` for entry revisions and local status, and `remove` with `--force` and `--keep-files`.
- Added `dependency push` with Git branch, direct update, patch, GitHub pull request, and filesystem overwrite delivery.
- Added shared repository caching, `--cache-dir`, and project-independent `cache clean`.
- Added per-project operation locks to coordinate concurrent project commands.
- Adopted `llm-tool-cli` 0.3.0 for shared CLI, path handling, cache storage, and process coordination.
- Provided human, LLM, and JSON Lines output, built-in usage documentation, and Docker development and release workflows.
