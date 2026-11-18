"""`benchctl start|stop|open|down`: bring the cluster, the governor and the web page up and down."""

import sqlite3
import sys

import typer

from .. import completion, inventory, stack, teleport, workbench
from .base import YES, _base_sim, _confirm, _profile_check
from .build import (
    ACCESS,
    ACCOUNTING,
    ADDONS,
    APP_MB_OPT,
    HARDWARE,
    LOGIN,
    MONITORING,
    TICKETING,
    WITH,
    WITHOUT,
    WORKLOAD,
    _login_modes,
    _with_parts,
)
from .groups import app
from .lab_support import _guard_lab_run, _lab_request
from .shellcompletion import _completion_install


def _launcher(*args: str) -> None:
    """The one-command launcher (machine check, venv, governor, cluster, web page) lives in scripts/benchctl-launch."""
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "benchctl-launch"
    raise typer.Exit(subprocess.run([str(script), *args], check=False).returncode)


@app.command()
def start(profile: str = typer.Option(None, "--profile", autocompletion=completion.profiles, callback=_profile_check,
                                      help="a size preset (default min)"),
          with_: list[str] = WITH, without: list[str] = WITHOUT, workload: list[str] = WORKLOAD,
          accounting: list[str] = ACCOUNTING, monitoring: list[str] = MONITORING, ticketing: list[str] = TICKETING,
          hardware: list[str] = HARDWARE, access: list[str] = ACCESS,
          addons: bool = ADDONS, login: list[str] = LOGIN, app_mb: int = APP_MB_OPT) -> None:
    """Everything in one step: machine check, governor, cluster, web page (the first run builds images). Add parts
    with --with (`--with accounting`), by category (`--ticketing jira --hardware bmc,bcm`) or all of them (`--addons`);
    on a running cluster it adds them and leaves the page up."""
    with_ = _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, addons, login)
    if profile and (with_ is not None or without or login is not None):
        typer.echo("choose one: --profile PRESET, or parts (--with, --without, category flags, --addons, --login)", err=True)
        raise typer.Exit(1)
    if with_ == [] or (with_ is None and login is not None):  # a flag that adds nothing still asks for a selection, not the min preset
        with_ = list(workbench.DEFAULT_PARTS)
    _offer_completion()
    modes = [] if login is None else ["--login", ",".join(_login_modes(login))]
    parts = [x for p in (with_ or []) for x in ("--with", p)] + [x for p in (without or []) for x in ("--without", p)] + modes
    try:
        saved = workbench.current(_base_sim())
    except workbench.SpecError:
        saved = None
    typer.echo(workbench.start_line(saved, profile, bool(parts)))
    _launcher("start", *(["--saved"] if workbench.reuses_saved(saved, profile, bool(parts)) else parts or ["--profile", profile or "min"]))


def _offer_completion() -> None:
    """Once, in a terminal: offer Tab completion for the user's shell. Asking is remembered; scripts are never asked."""
    shell = completion.detect_shell()
    flag = completion.asked_flag()
    if not shell or flag.exists() or completion.installed(shell) or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("asked\n")
        if typer.confirm(f"Turn on Tab completion for {shell}? (one line in your rc file; undo: benchctl completion remove)",
                         default=True):
            _completion_install(shell)
    except OSError as e:
        typer.echo(f"could not set up completion: {e}", err=True)


@app.command()
def stop(resource: str = typer.Argument(None, help="`lab NAME` stops that lab's replay run through the governor"),
         lab_name: str = typer.Argument(None, help="the lab whose run to stop (with `lab`)"),
         force: bool = typer.Option(False, "--force", help="while a config lab run is active: abort it without its restore (see `down --force`)"),
         yes: bool = typer.Option(False, "-y", "--yes", help="do not ask (BENCHCTL_AGENT=1 requires it with --force)")) -> None:
    """Stop the web page, the cluster and the governor this tool started (with `lab NAME`, only that config lab's replay run).

    `stop lab NAME` ends the run through the governor; an A/B run then puts the cluster back to its snapshot. While a lab run is
    active a bare `stop` refuses and names the two ways out (`stop lab NAME` then wait, or `--force`)."""
    if resource is None:
        _guard_lab_run("stop", force, yes)
        _launcher("stop", *(["--force"] if force else []), *(["--yes"] if yes else []))
    if resource != "lab" or not lab_name:
        typer.echo("usage: benchctl stop lab NAME   (or: benchctl stop)", err=True)
        raise typer.Exit(2)
    _lab_request({"cmd": "lab-stop", "name": lab_name},
                 lambda r: f"run {r['run']} of lab {lab_name} is stopping; `benchctl status` shows when it has ended")


@app.command("open")
def open_ui() -> None:
    """Print the web page's login link (and open it in a browser when there is one)."""
    _launcher("open")


FORCE_RUN = typer.Option(False, "--force", help="while a config lab run is active: abort it, skip its restore and keep the snapshot "
                         "(`benchctl remove lab NAME` then returns to the plain cluster); BENCHCTL_AGENT=1 also needs --yes")


@app.command()
def down(dry_run: bool = typer.Option(False, "-n", "--dry-run", help="list what would stop; change nothing"),
         yes: bool = YES, force: bool = FORCE_RUN) -> None:
    """Stop and remove the workbench compose project (nothing else). Refused while a config lab run is active (see --force)."""
    running = [r["name"] for r in inventory.service_rows() if r["state"] == "running"]
    if dry_run:
        typer.echo(f"would stop and remove {len(running)} containers: {', '.join(running) or 'none running'}")
        return
    _guard_lab_run("down", force, yes)
    if running:
        _confirm(f"This stops {len(running)} containers and ends open shells on the nodes.", yes)
    try:
        teleport.snapshot()  # while Teleport still runs: a later rebuild is the same cluster, so clients stay logged in
    except (OSError, sqlite3.Error) as e:
        typer.echo(f"note: Teleport's identity was not saved ({e}); a rebuild will be a new cluster", err=True)
    raise typer.Exit(0 if stack.down(say=typer.echo) else 1)
