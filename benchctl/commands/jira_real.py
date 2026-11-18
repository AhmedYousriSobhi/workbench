"""`benchctl refresh jira-real-licence` and `benchctl setup jira-real`."""

from pathlib import Path as _Path

import typer

from .. import jira_real
from .base import SuggestingGroup

refresh_app = typer.Typer(cls=SuggestingGroup, help="Renew something that runs out: the real Jira's test licence")


@refresh_app.command("jira-real-licence")
def refresh_jira_real_licence() -> None:
    """Give the real Jira another 3 hours: the test key counts from the start of Jira, so this restarts it (users, tickets and
    tokens stay) and prints the new end time. No sudo, no key to paste."""
    try:
        jira_real.refresh_licence(say=lambda m: typer.echo(m))
    except jira_real.JiraRealError as e:
        typer.echo(f"FAILED: {e}", err=True)
        raise typer.Exit(1) from e


setup_app = typer.Typer(cls=SuggestingGroup, help="Finish a started part: the real Jira")


@setup_app.command("jira-real")
def setup_jira_real(licence: str = typer.Option("", "--licence", help="a file with Atlassian's 3-hour Data Center test licence "
                                                f"(default ${jira_real.LICENSE_VAR} or {jira_real.LICENSE})")) -> None:
    """Finish the real Jira after `benchctl start --with jira-real`: the setup wizard, users alice to erin, project OPS, tickets,
    and an access token per user (written to compose/generated/secrets/jira-real.env, never printed). Safe to repeat."""
    try:
        text = jira_real.read_license(_Path(licence)) if licence else None
        jira_real.provision(licence=text, say=lambda m: typer.echo(m))
    except jira_real.JiraRealError as e:
        typer.echo(f"FAILED: {e}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Jira (real): {jira_real.URL}  sign in as admin, alice, bob, carol, dave or erin; passwords and tokens: {jira_real.ENV}")
