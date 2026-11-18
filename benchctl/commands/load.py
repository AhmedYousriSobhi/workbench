"""`benchctl load on|off`."""

import typer

from .. import stack
from .base import SuggestingGroup, _sim

load_app = typer.Typer(cls=SuggestingGroup, help="Simulated users submitting jobs")


@load_app.command("on")
def load_on(rate: float = typer.Option(6, "--rate", help="jobs per minute (Poisson arrivals)"),
            duration: float = typer.Option(120, "--duration", help="mean job length in seconds"),
            seed: int = typer.Option(None, "--seed", help="a fixed seed makes the arrivals repeatable"),
            personas: bool = typer.Option(False, "--personas", help="each user follows the job mix of the persona in sim.yaml, with some failing and cancelled jobs"),
            day: float = typer.Option(0, "--day", help="daily rhythm: seconds one simulated day lasts (60 to 86400; 0 = off), the arrival rate rises and falls"),
            mocks: bool = typer.Option(False, "--mocks", help="also start a demo client that calls Jira, BCM and the BMC farm, so the Traffic tab has requests"),
            mock_rate: float = typer.Option(30, "--mock-rate", help="demo client requests per minute"),
            tickets: bool = typer.Option(False, "--tickets", help="also start the ticket bot: fake engineers work the alert tickets in jira-mock (never a real Jira)"),
            ticket_every: float = typer.Option(120, "--ticket-every", help="ticket bot: mean seconds between actions (10 to 3600)"),
            ticket_resolve: float = typer.Option(600, "--ticket-resolve", help="ticket bot: seconds a ticket stays In Progress before it is resolved")) -> None:
    """Start the user simulator: alice and bob submit cpu jobs and GPU jobs at random, so squeue and the node states have a live queue."""
    from .. import workload

    try:
        cmds = workload.load_on(_sim(), rate, duration, seed, personas, day)
        if mocks:
            cmds += workload.mocks_on(_sim(), mock_rate)
        if tickets:
            cmds += workload.tickets_on(ticket_every, ticket_resolve, day, seed)
    except workload.WorkloadError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    for cmd in cmds:
        r = stack._run(cmd)
        if r.returncode and cmd not in (workload.load_off(), workload.mocks_off(), workload.tickets_off()):  # pkill finding nothing to stop is fine
            typer.echo(f"cannot start the load: {(r.stderr or r.stdout).strip()}", err=True)
            raise typer.Exit(1)
    typer.echo(f"load: on, about {rate:g} job(s) a minute, each about {duration:g} s (benchctl load off to stop)"
               + ("; personas on: job mixes by persona, some jobs fail or are cancelled" if personas else "")
               + (f"; daily rhythm: a day lasts {day:g} s" if day else "")
               + (f"; demo client for Jira, BCM and the BMC farm: about {mock_rate:g} request(s) a minute" if mocks else "")
               + (f"; ticket bot on jira-mock: about one action every {ticket_every:g} s" if tickets else ""))


@load_app.command("off")
def load_off() -> None:
    """Stop the user simulator (jobs already queued keep running)."""
    from .. import workload

    stack._run(workload.mocks_off())
    stack._run(workload.tickets_off())
    r = stack._run(workload.load_off())
    typer.echo("load: off" if r.returncode in (0, 1) else f"cannot stop the load: {(r.stderr or r.stdout).strip()}",
               err=r.returncode not in (0, 1))
    raise typer.Exit(0 if r.returncode in (0, 1) else 1)
