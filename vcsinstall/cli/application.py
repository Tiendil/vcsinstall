from llm_tool_cli.cli.application import create_app
from llm_tool_cli.cli.commands.initialization import register_init_command
from llm_tool_cli.cli.commands.skills import register_skill_command
from llm_tool_cli.cli.commands.version import register_version_command
from llm_tool_cli.cli.options import ConfigOption, ProtocolOption
from llm_tool_cli.core import settings

from vcsinstall.cli.commands import dependency, entries
from vcsinstall.cli.entities import CacheDirOption
from vcsinstall.skills import SkillDocument
from vcsinstall.workspace import Config

app = create_app(help="Install files into your repository from other repositories.")


@app.callback()
def initialize(
    protocol: ProtocolOption = None,
    config_path: ConfigOption = None,
    cache_dir: CacheDirOption = None,
) -> None:
    """Declare shared global options and the cache directory option."""


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
app.command("add")(entries.add_entry)


def main() -> None:
    settings.initialize(
        label=settings.ToolLabel("VCSINSTALL"), config_filename=settings.ConfigFilename("vcsinstall.toml")
    )
    app()
