"""`benchctl export profile|lab-report|test-report`."""

import time
from pathlib import Path as _Path

import typer

from .. import alerts, completion, contract, errors, export, inventory, lab_export, testreport
from .base import JSON, SuggestingGroup, _base_sim, _profile_check, _sim
from .get import _fault_rows, _page_run_state

export_app = typer.Typer(cls=SuggestingGroup, help="Write the workbench in a form another tool can import")


@export_app.command("profile")
def export_profile(out: str = typer.Option("bench-profile.json", "-o", "--output", help="file to write, or - for stdout"),
                   profile: str = typer.Option(None, "--profile", autocompletion=completion.profiles, callback=_profile_check, help="export this profile instead of the running containers"),
                   name: str = typer.Option("workbench", "--name", help="the cluster's name in the client"),
                   user: str = typer.Option(None, "--user", autocompletion=completion.users, help="SSH user, default the first user in sim.yaml"),
                   bmc: bool = typer.Option(False, "--bmc", help="also list one BMC target per node"),
                   bmc_via: str = typer.Option("ip", "--bmc-via", help="a BMC's address in the file: ip (10.148.x.y, reachable from here and "
                                                                       "from the login node) or name (bmc-NODE, only from the login node)"),
                   loopback: bool = typer.Option(False, "--loopback", help="write 127.0.0.1 and the published ports (a client that cannot "
                                                                           "route to the cluster network) instead of the container IPs"),
                   configured: bool = typer.Option(False, "--configured", help="list what the selection says, not the running containers"),
                   fmt: str = typer.Option(None, "--format", help="client format ID[:VERSION]; else $BENCH_EXPORT_FORMAT "
                                                                 "or .git/export-format")) -> None:
    """Write a profile file the monitoring client can import: SSH, Slurm, Grafana, Jira, storage (no secrets)."""
    from pathlib import Path

    base = _base_sim()
    try:
        live = None if configured or profile else export.running(base)
        if live:
            (sim, services), prof = live, "running containers"
        else:
            sim, services, prof = export.configured(base, profile)
            if not configured and not profile:
                typer.echo("note: no containers are running, so this lists the configured cluster "
                           f"({prof}); start it first for the live one", err=True)
        text = export.to_json(export.build(sim, services, name, bmc, user, live=bool(live), bmc_via=bmc_via, loopback=loopback), export.format_id(fmt))
    except export.ExportError as e:
        typer.echo(f"cannot export: {e}", err=True)
        raise typer.Exit(1) from e
    for w in export.warnings(services, sim):
        typer.echo(f"warning: {w}", err=True)
    if out == "-":
        typer.echo(text, nl=False)
        return
    Path(out).write_text(text)
    typer.echo(f"wrote {out} (profile {prof}). Import it in the client, then set what a file never holds:\n"
               "  SSH key: benchctl ssh-key install, then pick ~/.ssh/workbench_ed25519\n"
               "  Grafana token: cat compose/generated/secrets/grafana_viewer_token (new after every `up`)"
               + ("\n  BMC password: the part after 'admin:' in compose/generated/secrets/bmc_credentials" if bmc else ""))


def _best_effort(read):
    """One part of the report that only the CLI can read: None when it cannot (never an empty answer in its place)."""
    import subprocess

    try:
        return read()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, alerts.NotRunning):
        return None


@export_app.command("lab-report")
def export_lab_report(name: str = typer.Argument(..., help="a lab made with `benchctl import lab`"),
                      run: str = typer.Argument("last", help="a run folder name (see benchctl get lab NAME), or last"),
                      anonymize: bool = typer.Option(False, "--anonymize", help="replace production node names with lab-node-NNN and user names with <user>"),
                      json_: bool = JSON,
                      out: str = typer.Option(None, "-o", "--output", help="file to write (default: print on stdout; - is stdout)")) -> None:
    """The config lab's report of one run (the verdict, findings, wait by partition, suggestions), to attach to a ticket. Markdown; `--json` gives report.json with a contract_version. `--anonymize` maps production node names to their lab names and user names to <user>; the mapping is never written. Reads the run's report files only. Exit 1 when the lab, the run or its report is missing."""
    try:
        data, markdown = lab_export.export(name, run, anonymize)
    except lab_export.ExportError as e:
        typer.echo(errors.render("WB-NO-LAB-REPORT", str(e)), err=True)
        raise typer.Exit(1) from e
    text = contract.dumps(data) + "\n" if contract.wanted("table", json_) == "json" else markdown
    if out is None or out == "-":
        typer.echo(text, nl=False)
        return
    _Path(out).write_text(text)


@export_app.command("test-report")
def export_test_report(run: str = typer.Argument("last", help="last, or the name of the scenario the page ran last"),
                       json_: bool = JSON,
                       out: str = typer.Option(None, "-o", "--output", help="file to write (default: print on stdout; - is stdout)")) -> None:
    """A report of the web page's last scenario run, to attach to a bug report: what was injected and when, each expectation, the calls and errors per mock during the run, and (as at export) node states, faults and firing alerts. Markdown; `--json` gives the same data with a contract_version. Left out: request bodies, tokens, hostnames, paths, a step's raw action, capture contents. The page keeps the run in memory only: play it with the page up (`benchctl open`, or `benchctl scenario run NAME`). Exit 1 when the page is not running or has no finished run of that name."""
    try:
        state = _page_run_state()
    except (OSError, ValueError) as e:
        typer.echo(errors.render("WB-PART-NOT-RUNNING", f"the web page does not answer ({e}); start it with: benchctl start"), err=True)
        raise typer.Exit(1) from e
    try:
        data, markdown = testreport.report(
            state, run, time.time(), nodes=lambda: _best_effort(lambda: inventory.node_rows(_sim())), faults=lambda: _best_effort(_fault_rows),
            alerts=lambda: _best_effort(alerts.fetch))
    except testreport.NoRun as e:
        typer.echo(errors.render("WB-NO-RUN", str(e)), err=True)
        raise typer.Exit(1) from e
    text = contract.dumps(data) + "\n" if contract.wanted("table", json_) == "json" else markdown
    if out is None or out == "-":
        typer.echo(text, nl=False)
        return
    _Path(out).write_text(text)
    typer.echo(f"wrote {out} (test report of {data['scenario']}: {data['verdict']})")
