"""`benchctl run ticket-team|ticket-watch` (mounted on the `run` group, which `cli.py` owns)."""

import typer

from .. import stack, ticketbot

_TARGET = typer.Option("mock", "--target", help="mock (the Jira mock) or real (the Data Center part)")


def _driver(target: str):
    from .jira_driver import JiraDriver, JiraError
    try:
        return JiraDriver.for_target(target)
    except JiraError as e:
        typer.echo(f"FAILED: {e}", err=True)
        raise typer.Exit(1) from None


def run_ticket_team(target: str = _TARGET, every: float = typer.Option(45, "--every", help="seconds between changes"),
                    count: int = typer.Option(0, "--count", help="stop after N changes (default: until Ctrl-C)")) -> None:
    """A team works the tickets: moves them along, comments, reassigns, reopens, files follow-ups. Same on either Jira."""
    driver = _driver(target)
    typer.echo(f"ticket team on the {target} Jira, a change about every {every:g} s (Ctrl-C stops)")
    try:
        ticketbot.team(driver, every, count or None, say=typer.echo)
    except KeyboardInterrupt:
        typer.echo("stopped")
    except OSError as e:
        typer.echo(f"cannot reach the {target} Jira: {e}", err=True)
        raise typer.Exit(1) from e


def run_ticket_watch(target: str = _TARGET, every: float = typer.Option(10, "--every", help="seconds between polls")) -> None:
    """Watch Slurm and file a ticket when a node goes out of service, resolve it when it returns (what node_watch does for the mock)."""
    driver = _driver(target)
    typer.echo(f"watching sinfo for the {target} Jira, every {every:g} s (Ctrl-C stops)")
    try:
        ticketbot.watch_nodes(driver, stack.PROJECT, every, say=typer.echo)
    except KeyboardInterrupt:
        typer.echo("stopped")


def register(run_app: typer.Typer) -> None:
    """Add the two verbs to the `run` group (owned by `cli.py`), after `run lab`."""
    run_app.command("ticket-team")(run_ticket_team)
    run_app.command("ticket-watch")(run_ticket_watch)
