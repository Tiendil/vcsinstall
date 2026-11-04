from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path

from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import ResolvedProjectPath, join_path, project_path_id_from_resolved, resolve_project_root

from vcsinstall.domain import ContentKind, Dependency, RepositoryLocator, SourceRevision
from vcsinstall.filesystem import FSClient
from vcsinstall.hosting import errors as publication_errors
from vcsinstall.hosting.contributions import covering_changes
from vcsinstall.hosting.entities import (
    Contribution,
    DeliveryMode,
    DeliveryResult,
    HostingProvider,
    PublicationRequest,
    PushOptions,
)
from vcsinstall.sources import SourceAdapter, errors


class FilesystemHostingAdapter:
    __slots__ = ()

    def validate_artifact(self, dependency: Dependency, path: ResolvedProjectPath | None) -> Result[None]:
        """Reject artifacts for filesystem hosting, which supports only overwrite.

        An absent artifact path passes; a requested patch file fails without source access.
        """
        if path is not None:
            return Err([InvalidArguments(reason="Filesystem hosting does not export patch artifacts.")])
        return Ok(None)

    def validate_options(self, dependency: Dependency, options: PushOptions) -> Result[None]:
        """Accept only explicit overwrite without VCS options.

        For example, overwrite passes and patch fails before reading local or source content.
        """
        if options.mode != DeliveryMode.overwrite:
            return Err([InvalidArguments(reason=f"Filesystem hosting does not support mode {options.mode}.")])
        return Ok(None)

    @unwrap_to_error
    def publish(self, request: PublicationRequest, *, source: SourceAdapter) -> Result[DeliveryResult]:
        with self.acquire(request.dependency, refresh=False) as acquired:
            root = resolve_project_root(acquired.unwrap()).unwrap()
            filesystem = FSClient(root=root, readonly=False)
            # A complete readable source is required to report the resulting fingerprint.
            filesystem.inspect_tree().unwrap()
            changes = covering_changes(request.changes)
            for change in changes:
                self._validate_mapping(request, change, filesystem=filesystem).unwrap()

            completed: list[str] = []
            for change in changes:
                result = self._overwrite(request, change, filesystem=filesystem)
                if result.is_err():
                    return Err(
                        [
                            publication_errors.PublicationFailed(
                                repository=request.dependency.repository,
                                reason=f"Source writes at {change.source_path} may be partial.",
                                completed_effects=completed,
                                uncertain=True,
                            ),
                            *result.unwrap_err(),
                        ]
                    )
                completed.append(f"Overwrote {change.source_path}")

            revision = source.resolve(request.dependency.replace(selector=None), root)
            if revision.is_err():
                return Err(
                    [
                        publication_errors.PublicationFailed(
                            repository=request.dependency.repository,
                            reason="Source writes completed but fingerprint verification failed.",
                            completed_effects=completed,
                        ),
                        *revision.unwrap_err(),
                    ]
                )
            return Ok(
                DeliveryResult(
                    provider=HostingProvider.filesystem,
                    repository=request.dependency.repository,
                    revision=revision.unwrap().revision,
                )
            )

    @unwrap_to_error
    def _validate_mapping(  # noqa: CCR001 — Check containment, root preservation, and required parents before writes.
        self, request: PublicationRequest, change: Contribution, *, filesystem: FSClient
    ) -> Result[None]:
        """Protect preserved source parents and project state before any overwrite.

        A selected directory may replace all its children; a file outside its root
        cannot be removed to create a required parent. Replacing the source root by a file fails.
        """
        root = filesystem.root
        target = filesystem.resolve_path(change.source_path).unwrap()
        if any(
            target == path or target.is_relative_to(path) or path.is_relative_to(target)
            for path in request.protected_paths
        ):
            return Err(
                [
                    publication_errors.PreparationFailed(
                        repository=request.dependency.repository,
                        reason=f"Source mapping {change.source_path} overlaps protected project state.",
                    )
                ]
            )
        if target == root and change.content.kind == ContentKind.file:  # type: ignore[comparison-overlap]
            return Err(
                [
                    publication_errors.PreparationFailed(
                        repository=request.dependency.repository, reason="The source root must remain a directory."
                    )
                ]
            )
        for parent in target.parents:
            if parent == root or not parent.is_relative_to(root):  # type: ignore[comparison-overlap]
                break
            identifier = project_path_id_from_resolved(ResolvedProjectPath(parent), root)
            if filesystem.inspect_kind(identifier).unwrap() == ContentKind.file:
                return Err(
                    [
                        publication_errors.PreparationFailed(
                            repository=request.dependency.repository,
                            reason=f"An unselected parent obstructs {change.source_path}.",
                        )
                    ]
                )
        return Ok(None)

    @unwrap_to_error
    def _overwrite(self, request: PublicationRequest, change: Contribution, *, filesystem: FSClient) -> Result[None]:
        filesystem.remove(change.source_path, preserve_root=change.source_path == "@/").unwrap()
        local = FSClient(root=request.local_root)
        for file in change.content.files:
            source_path = join_path(change.destination, file.path)
            target = join_path(change.source_path, file.path)
            filesystem.write_stream(target, partial(local.copy_file, source_path), executable=file.executable).unwrap()
        verified = filesystem.inspect_tree(change.source_path).unwrap()
        if verified.file_hashes != change.content.file_hashes:
            return Err(
                [
                    publication_errors.PreparationFailed(
                        repository=request.dependency.repository,
                        reason=f"Written source content at {change.source_path} failed verification.",
                    )
                ]
            )
        return Ok(None)

    def probe_repository(self, repository: RepositoryLocator) -> Result[bool]:
        return Ok(False)

    @contextmanager
    def acquire(
        self, dependency: Dependency, *, revision: SourceRevision | None = None, refresh: bool = True
    ) -> Iterator[Result[Path]]:
        yield self._acquire(dependency)

    @unwrap_to_error
    def _acquire(self, dependency: Dependency) -> Result[Path]:
        client = FSClient(root=resolve_project_root(Path(dependency.repository)).unwrap(), readonly=True)
        if client.inspect_kind().unwrap() != ContentKind.directory:
            return Err(
                [
                    errors.RepositoryAccess(
                        vcs=dependency.vcs,
                        repository=dependency.repository,
                        reason="Source must be an existing directory.",
                    )
                ]
            )
        return Ok(client.root)
