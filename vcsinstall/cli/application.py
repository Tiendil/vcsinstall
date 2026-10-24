from llm_tool_cli.cli.application import create_app
from llm_tool_cli.cli.commands.skills import register_skill_command
from llm_tool_cli.cli.commands.version import register_version_command
from llm_tool_cli.core import settings

from vcsinstall.skills import SkillDocument

app = create_app(help="Install files into your repository from other repositories.")
register_skill_command(app, package="vcsinstall.skills", documents=SkillDocument)
register_version_command(app, distribution="vcsinstall")


def main() -> None:
    settings.initialize(
        label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
    )
    app()
