"""`benchctl add invite USER`: a one-time Teleport sign-up link."""

import typer

from .. import stack, teleport
from .base import _sim
from .groups import add_app


@add_app.command("invite")
def add_invite(user: str = typer.Argument(..., help="a user from sim.yaml")) -> None:
    """Make a one-time link to choose USER's Teleport password, for `tsh login`: `benchctl add invite alice`. Open it in a
    browser once (the page is served with a self-signed certificate). Needs Teleport running (`--with teleport`)."""
    if user not in [u["name"] for u in _sim().get("users", [])]:
        typer.echo(f"{user!r} is not a user in sim.yaml", err=True)
        raise typer.Exit(2)
    try:
        typer.echo(teleport.invite(user, stack._run, stack.dc))
        teleport.snapshot()  # the user's password is chosen in the browser; the next `down` saves that too
    except teleport.TeleportError as e:
        typer.echo(f"{e}\nIs Teleport running? Start it with `benchctl up --with teleport` (see `benchctl get services`).", err=True)
        raise typer.Exit(1) from e
