"""`benchctl --help` lists verbs in the order their modules register them (issue 857): moving a verb to another module, or
reordering the imports in `benchctl/commands/__init__.py`, must not reorder what a user reads."""

import click
import typer.main

from benchctl import cli

TOP = ["check", "doctor", "preflight", "status", "diag", "compose", "plan", "up", "apply", "start", "stop", "open", "down", "submit", "exec", "ssh", "logs", "scale", "completion", "get", "set", "clear", "add", "remove", "validate", "reload", "refresh", "setup", "load", "run", "serve", "describe", "wait", "import", "diff", "export", "ssh-key", "scenario", "measure"]
GROUPS = {
    "get": ["ticket-bot", "nodes", "services", "workbench", "profiles", "checks", "trace", "slurm-stats", "slurm-checksum", "capture", "lab", "endpoints", "faults", "traffic", "alerts", "scenario-status", "scenarios"],
    "set": ["node", "netem", "ticket-bot", "slurmd", "slurmctld", "k8s-step", "check", "jira-activity"],
    "clear": ["faults", "netem"],
    # verbs several modules add to: the import order in commands/__init__.py is what keeps these lists
    "add": ["check", "invite", "candidate"],
    "remove": ["jobs", "teleport-state", "jira-real-state", "storage-state", "check", "lab"],
    "validate": ["checks", "lab"],
    "run": ["lab", "ticket-team", "ticket-watch"],
    "export": ["profile", "lab-report", "test-report"],
    "wait": ["ready", "node-state", "alert", "scenario"],
}


def _names(cmd):
    return cmd.list_commands(click.Context(cmd))


def test_top_level_verbs_keep_their_help_order():
    assert _names(typer.main.get_command(cli.app)) == TOP


def test_group_verbs_keep_their_help_order():
    root = typer.main.get_command(cli.app)
    for group, expected in GROUPS.items():
        assert _names(root.get_command(click.Context(root), group)) == expected, group
