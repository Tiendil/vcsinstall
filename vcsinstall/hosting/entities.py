from enum import StrEnum
from pathlib import Path
from typing import Annotated

import pydantic
from llm_tool_cli.core.entities import BaseEntity
from llm_tool_cli.paths import PathInput, ProjectPathId, ProjectRootPath, ResolvedProjectPath

from vcsinstall.domain import ContentHash, Dependency, RepositoryLocator, SelectorValue, SourcePath, SourceRevision
from vcsinstall.filesystem import ContentTree


class DeliveryMode(StrEnum):
    pr = "pr"
    branch = "branch"
    direct = "direct"
    patch = "patch"
    overwrite = "overwrite"


class HostingProvider(StrEnum):
    git = "git"
    github = "github"
    filesystem = "filesystem"


class PushOptions(BaseEntity):
    """Invocation-local publication choices, independent of installation state."""

    mode: DeliveryMode
    branch: SelectorValue | None = None
    push_repository: RepositoryLocator | None = None
    base: SelectorValue | None = None
    patch_file: PathInput | None = None
    message: Annotated[str, pydantic.StringConstraints(strip_whitespace=False)] = "Update shared files"


class Contribution(BaseEntity):
    """A complete inspected local mapping and its trustworthy installation hashes."""

    source_path: SourcePath
    destination: ProjectPathId
    content: ContentTree
    baseline: dict[ProjectPathId, ContentHash]


class PublicationRequest(BaseEntity):
    dependency: Dependency
    revision: SourceRevision
    local_root: ProjectRootPath
    protected_paths: tuple[Path, ...]
    changes: tuple[Contribution, ...]
    options: PushOptions
    patch_file: ResolvedProjectPath | None = None


class DeliveryResult(BaseEntity):
    provider: HostingProvider
    repository: RepositoryLocator | None = None
    branch: SelectorValue | None = None
    revision: SourceRevision | None = None
    base_branch: SelectorValue | None = None
    review_url: str | None = None
    patch_file: ResolvedProjectPath | None = None
