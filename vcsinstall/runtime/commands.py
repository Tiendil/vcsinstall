from llm_tool_cli.core.result import Ok, Result

from vcsinstall.domain import DependencyName, RepositoryLocator


def dependency_add(name: DependencyName, repository: RepositoryLocator) -> Result[None]:
    """Reserved command path; loading is implemented, dependency mutation is pending."""
    return Ok(None)
