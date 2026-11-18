"""`benchctl remove jobs|teleport-state|jira-real-state|storage-state`: forget state (`remove check` is in nhc.py, `remove lab` in lab.py; the import order in __init__.py keeps their order in --help)."""

import typer

from .. import inventory, jira_real, storage, teleport
from .base import YES, _confirm, _daemon, _sim
from .groups import remove_app


@remove_app.command("jobs")
def remove_jobs(yes: bool = YES) -> None:
    """Cancel every job of the simulated users (scancel); the nodes go back to idle."""
    _confirm("This cancels every job of the simulated users.", yes, ask=False)
    _daemon({"cancel": True}, "jobs cancelled")


@remove_app.command("teleport-state")
def remove_teleport_state(yes: bool = YES) -> None:
    """Forget the Teleport cluster: the next `benchctl up` makes a new one (new certificate authority, no passwords), and every
    client must `tsh logout`, then `benchctl add invite USER`, then log in again. Stop the stack first (`benchctl down`)."""
    if any(r["name"] == "teleport" and r["state"] == "running" for r in inventory.service_rows()):
        typer.echo("Teleport is running: run `benchctl down` first (it saves the identity again), then repeat this.", err=True)
        raise typer.Exit(1)
    _confirm("This deletes Teleport's data and its saved identity: every Teleport user and password is lost.", yes)
    gone = teleport.reset()
    typer.echo("removed: " + ", ".join(str(p) for p in gone) if gone else "nothing to remove: no Teleport state")
    typer.echo("next: benchctl up --with teleport; then each client: tsh logout --proxy=10.141.255.30:3080 --insecure, "
               "benchctl add invite USER, log in")


@remove_app.command("jira-real-state")
def remove_jira_real_state(yes: bool = YES) -> None:
    """Forget the real Jira: remove its containers and data, so the next start is a fresh one with a fresh 3-hour licence. The
    mock is not touched."""
    _confirm("This deletes the real Jira's data: its users, tickets and tokens.", yes)
    jira_real.reset()
    typer.echo("removed the real Jira's containers and data")
    typer.echo("next: benchctl start --with jira-real; benchctl setup jira-real")


@remove_app.command("storage-state")
def remove_storage_state(yes: bool = YES) -> None:
    """Empty the shared scratch (and any other shared mount): removes its Docker volume, so the datasets and job output in it are lost.
    A mount that names a `host_path` is a directory of yours and is never touched. Stop the stack first (`benchctl down`)."""
    if any(r["state"] == "running" for r in inventory.service_rows()):
        typer.echo("The cluster is running: run `benchctl down` first, then repeat this.", err=True)
        raise typer.Exit(1)
    _confirm("This deletes everything in the shared volumes (datasets, job output).", yes)
    gone = storage.reset(_sim())
    typer.echo("removed: " + ", ".join(gone) if gone else "nothing to remove: no shared volume")
