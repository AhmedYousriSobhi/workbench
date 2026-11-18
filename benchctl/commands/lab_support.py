"""What the lab verbs and the cluster verbs both need: the governor round trip for lab runs and the guard `down`/`stop` apply."""

import os
from collections.abc import Callable

import typer

from .. import checks, lab, lab_apply, lab_candidate, workbench
from .base import _base_sim, _confirm

RUN_REFUSED = 3  # exit code of `down` and `stop` refused because a lab run is active (scripts/benchctl-launch checks it)


def _active_lab_run() -> dict | None:
    """The governor's `lab` block when a run is preparing, running or recovering; None when there is none or no governor."""
    try:
        block = checks.governor_request({"cmd": "status"}, os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET)).get("lab")
    except (OSError, ValueError):
        return None
    return block if isinstance(block, dict) and block.get("active") else None


def _guard_lab_run(verb: str, force: bool, yes: bool) -> None:
    """`down` and bare `stop` while a lab run is active (owner decision, issue 554 round 3): refused, naming the two ways out;
    `--force` aborts the run without its restore (the snapshot is kept). `--yes` never overrides; with BENCHCTL_AGENT=1 `--force`
    also needs `--yes`."""
    run = _active_lab_run()
    if not run:
        return
    name, rid = run.get("name"), run.get("run") or "(preparing)"
    if not force:
        typer.echo(f"refused: lab run {rid} of {name} is {run.get('state')}. Either `benchctl stop lab {name}`, wait until "
                   f"`benchctl status` shows it ended, then `benchctl {verb}`; or `benchctl {verb} --force`, which aborts the run, skips its "
                   f"restore and keeps the snapshot (`benchctl remove lab {name}` then returns to the plain cluster)", err=True)
        raise typer.Exit(RUN_REFUSED)
    _confirm(f"This aborts lab run {rid} of {name} without restoring its snapshot.", yes, ask=False)
    try:
        r = checks.governor_request({"cmd": "lab-stop", "name": name, "force": True}, os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET))
    except OSError as e:
        typer.echo(f"the governor stopped answering: {e}", err=True)
        raise typer.Exit(1) from e
    if not r.get("ok"):
        typer.echo(str(r.get("error")), err=True)
        raise typer.Exit(1)
    typer.echo(f"lab run {rid} of {name} aborted without its restore; the snapshot is kept: `benchctl remove lab {name}` returns to the plain cluster")


def _lab_request(req: dict, ok_text: Callable[[dict], str]) -> dict:
    """Send a lab run command to the governor; one line for each answer (W-10)."""
    sock = os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET)
    try:
        r = checks.governor_request(req, sock, timeout=60)
    except OSError:
        typer.echo("the governor is not reachable: lab runs go through it. Start it with ./scripts/benchctl start", err=True)
        raise typer.Exit(1) from None
    if not r.get("ok"):
        typer.echo(str(r.get("error", "refused")), err=True)
        raise typer.Exit(1)
    typer.echo("the governor is in dry-run mode: nothing was started" if r.get("dry_run") else ok_text(r))
    return r


def _lab_env() -> lab_apply.Env:
    return lab_apply.Env(sim=_base_sim(), running=lambda s: s in workbench.running_services(), say=typer.echo)


def _lab_run(op: str, name: str, side: str, execute: bool) -> None:
    if execute:  # the governor's child process
        raise typer.Exit(0 if lab_apply.execute(op, name, side, _lab_env()) else 1)
    try:
        lab.valid_name(name)
        lab_candidate.check_side(side)
    except lab.LabError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    req = {"cmd": "lab-apply", "name": name, "side": side} if op == "apply" else {"cmd": "lab-restore", "name": name}
    ok, lines = lab_apply.ask_governor(req, name, lambda r: checks.governor_request(r, checks.GOV_SOCKET, timeout=10))
    for ln in lines:
        typer.echo(ln, err=not ok)
    raise typer.Exit(0 if ok else 1)
