"""benchctl: the one controller command (grammar `VERB [RESOURCE] [NAME]`, ADR 0015).

This file only builds `app` and mounts the command modules of `benchctl/commands/` in the order `--help` lists them (issue 857).
Add a verb in the module of its domain, not here. `python -m benchctl.cli` and the `benchctl` entry point run `app`.
"""

import sys  # noqa: F401  (tests reach `cli.sys`, `cli.time`, `cli.typer` and the modules below through this module)
import time  # noqa: F401

import typer
import typer.completion

from . import checks, commands, compose, diag, inventory, scenario, stack, teleport, workbench  # noqa: F401
from .commands import groups
from .commands.base import _base_sim  # noqa: F401  (`agents.py` reads `cli._base_sim`)
from .commands.groups import app

typer.completion.completion_init()  # register the shell handlers; the options are ours (`benchctl completion`)


@app.callback()
def _main() -> None:
    pass


app.add_typer(commands.shellcompletion.completion_app, name="completion")
app.add_typer(commands.get.get_app, name="get")
app.add_typer(commands.adjust.set_app, name="set")
app.add_typer(commands.adjust.clear_app, name="clear")
app.add_typer(groups.add_app, name="add")
app.add_typer(groups.remove_app, name="remove")
app.add_typer(groups.validate_app, name="validate")
app.add_typer(commands.nhc.reload_app, name="reload")
app.add_typer(commands.jira_real.refresh_app, name="refresh")
app.add_typer(commands.jira_real.setup_app, name="setup")
app.add_typer(commands.load.load_app, name="load")
app.add_typer(groups.run_app, name="run")
commands.tickets.register(groups.run_app)
app.add_typer(commands.serve.serve_app, name="serve")
app.add_typer(commands.describe.describe_app, name="describe")
app.add_typer(commands.wait.wait_app, name="wait")
app.add_typer(commands.lab.import_app, name="import")
app.add_typer(commands.lab.diff_app, name="diff")
app.add_typer(commands.export.export_app, name="export")
app.add_typer(commands.sshkey.ssh_key_app, name="ssh-key")
app.add_typer(commands.scenario.scenario_app, name="scenario")
app.add_typer(commands.measure.measure_app, name="measure")


if __name__ == "__main__":
    app(prog_name="benchctl")
