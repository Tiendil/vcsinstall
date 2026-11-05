from llm_tool_cli.cli.application import create_app
from llm_tool_cli.cli.commands.initialization import register_init_command
from llm_tool_cli.cli.commands.skills import register_skill_command
from llm_tool_cli.cli.commands.version import register_version_command
from llm_tool_cli.core import settings
from llm_tool_cli.core.entities import ToolName

from vcsinstall.cli.commands import cache, dependency, entries
from vcsinstall.skills import SkillDocument
from vcsinstall.workspace import Config, project_operation

app = create_app(help="Install files into your repository from other repositories.")


register_skill_command(app, package="vcsinstall.skills", documents=SkillDocument)
register_version_command(app, distribution="vcsinstall")
register_init_command(
    app,
    config_class=Config,
    package="vcsinstall.workspace",
    template="base_config.toml",
    help="Create starter project configuration.",
    operation_scope=lambda cwd, path: project_operation(cwd=cwd, path=path, initialize=True),
)
app.add_typer(cache.app, name="cache")
app.add_typer(dependency.app, name="dependency")
app.command("add")(entries.add_entry)
app.command("remove")(entries.remove_entries)
app.command("install")(entries.install_entries)
app.command("list")(entries.list_entries)


def main() -> None:
    settings.initialize(name=ToolName("vcsinstall"), config_filename=settings.ConfigFilename("vcsinstall.toml"))
    app()
