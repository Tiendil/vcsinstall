from llm_tool_cli.cli.application import create_app
from llm_tool_cli.cli.commands.initialization import register_init_command
from llm_tool_cli.cli.commands.skills import register_skill_command
from llm_tool_cli.cli.commands.version import register_version_command
from llm_tool_cli.core import settings

from vcsinstall.cli.commands import dependency
from vcsinstall.skills import SkillDocument
from vcsinstall.workspace import Config

app = create_app(help="Install files into your repository from other repositories.")
register_skill_command(app, package="vcsinstall.skills", documents=SkillDocument)
register_version_command(app, distribution="vcsinstall")
register_init_command(
    app,
    config_class=Config,
    package="vcsinstall.workspace",
    template="base_config.toml",
    help="Create starter project configuration.",
)
app.add_typer(dependency.app, name="dependency")


def main() -> None:
    settings.initialize(
        label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
    )
    app()
