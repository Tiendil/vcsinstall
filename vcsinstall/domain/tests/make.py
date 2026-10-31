from vcsinstall.domain import Dependency, VersionSelector


def selector(kind: str, value: str) -> VersionSelector:
    return VersionSelector.model_validate({"kind": kind, "value": value})


def dependency(**changes: object) -> Dependency:
    return Dependency.model_validate({"vcs": "git", "repository": "source", **changes})
