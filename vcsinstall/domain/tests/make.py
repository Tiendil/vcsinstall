from vcsinstall.domain import Dependency


def dependency(**changes: object) -> Dependency:
    return Dependency.model_validate({"vcs": "git", "repository": "source", **changes})
