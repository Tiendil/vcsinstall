from pathlib import Path
from urllib.parse import urlsplit

import pydantic
from llm_tool_cli.cli.errors import InvalidArguments
from llm_tool_cli.core.result import Err, Ok, Result, unwrap_to_error
from llm_tool_cli.paths import resolve_project_root
from llm_tool_cli.paths.errors import PathResolutionFailed

from vcsinstall.domain import RepositoryLocator, SelectorKind, SelectorValue, VersionSelector, urls
from vcsinstall.sources.entities import SourceRequest


@unwrap_to_error
def normalize_local_repository(  # noqa: CCR001 — Local file-URL validation and canonical root resolution.
    repository: RepositoryLocator, *, cwd: Path
) -> Result[RepositoryLocator | None]:
    """Share local path and file-URL handling between native source adapters."""
    value = str(repository)
    if not value:
        return Ok(None)
    if urls.is_url(value):
        try:
            url = urlsplit(value)
        except ValueError:
            return Ok(None)
        if url.scheme == "file":
            if url.netloc not in ("", "localhost") or url.query:
                return Ok(None)
            value = urls.decode_url(url.path)
        else:
            return Ok(None)
    try:
        path = Path(value).expanduser()
        root = resolve_project_root(path if path.is_absolute() else cwd / path).unwrap()
    except ValueError:
        return Ok(None)
    except (OSError, RuntimeError) as exc:
        return Err([PathResolutionFailed(path=value, reason=str(exc)).with_cause(exc)])
    return Ok(RepositoryLocator(str(root)))


def parse_repository(repository: RepositoryLocator) -> Result[SourceRequest]:  # noqa: CCR001
    """Parse a repository locator and its optional source-version fragment."""
    selector = None
    native = str(repository)
    if not native:
        return Err([InvalidArguments(reason="A nonempty repository locator is required.")])
    try:
        if urls.is_url(native):
            try:
                url = urlsplit(native)
            except ValueError as exc:
                return Err([InvalidArguments(reason=str(exc))])
            native = native.split("#", 1)[0]
            if "#" in repository:
                fragment = url.fragment
                if not fragment or "&" in fragment or "#" in fragment:
                    return Err(
                        [
                            InvalidArguments(
                                reason="Supply one nonempty source-version fragment, without combining selectors."
                            )
                        ]
                    )
                if "=" in fragment:
                    kind, value = fragment.split("=", 1)
                    if kind not in ("branch", "tag", "rev") or not value or "=" in value:
                        return Err(
                            [InvalidArguments(reason="Expected #branch=BRANCH, #tag=TAG, #rev=REVISION, or #REF.")]
                        )
                    selector = VersionSelector(
                        kind={"branch": SelectorKind.branch, "tag": SelectorKind.tag, "rev": SelectorKind.revision}[
                            kind
                        ],
                        value=SelectorValue(urls.decode_url(value)),
                    )
                else:
                    selector = VersionSelector(
                        kind=SelectorKind.unresolved, value=SelectorValue(urls.decode_url(fragment))
                    )
        request = SourceRequest(repository=RepositoryLocator(native), selector=selector)
    except pydantic.ValidationError as exc:
        return Err([InvalidArguments(reason=str(exc)).with_cause(exc)])
    return Ok(request)
