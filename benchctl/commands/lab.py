"""Config lab: `import lab`, `diff lab`, `add candidate`, `remove lab`, `validate lab`, `run lab` (`get lab` is in get.py, `apply lab` in build.py, `stop lab` in lifecycle.py)."""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path as _Path

import typer

from .. import checks, lab, lab_candidate, lab_diff, lab_validate
from .base import OUTPUT, SuggestingGroup, _base_sim
from .groups import add_app, remove_app, run_app, validate_app
from .lab_support import _active_lab_run, _lab_env, _lab_request, _lab_run


def _lab_wait(sock: str, job: int, timeout: float = 600) -> str | None:
    """Wait for the governor's lab-validate change to end; None when it ended, else why not."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        time.sleep(1)
        try:
            ch = checks.governor_request({"cmd": "status"}, sock).get("change")
        except OSError as e:
            return f"the governor stopped answering: {e}"
        if ch and ch.get("job") == job and ch.get("state") != "running":
            return None
    return f"the validation did not finish within {int(timeout)} s"


@validate_app.command("lab")
def validate_lab(name: str = typer.Argument(..., help="a lab made with `benchctl import lab`"),
                 side: str = typer.Option("baseline", "--side", help="baseline (the imported configuration); candidate comes with issue 553"),
                 run_here: bool = typer.Option(False, "--run-here", hidden=True, help="run the checks in this process (the governor uses it)"),
                 output: str = OUTPUT) -> None:
    """Check the lab's imported configuration with the real tools (a throwaway slurmctld, sacctmgr load, promtool, kubectl dry-run, Teleport roles, secret scan, versions). Runs through the governor at L0-L1. Exit 0 passed (skips are listed), 1 failed, 3 not available."""
    if side not in lab_validate.SIDES:
        typer.echo(f"--side must be baseline or candidate, not {side!r}", err=True)
        raise typer.Exit(1)
    if side == "candidate":
        typer.echo("the candidate side is not available yet: it comes with issue 553 (diff and apply); `--side baseline` checks the imported configuration")
        raise typer.Exit(lab_validate.NOT_AVAILABLE)
    try:
        if not lab.exists(name):
            raise lab.LabError(f"no lab {name}: import it first (benchctl import lab {name} --from DIR)")
        if run_here:
            report = lab_validate.run(name, _base_sim(), side)
        else:
            sock = os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET)
            try:
                r = checks.governor_request({"cmd": "lab-validate", "name": name, "side": side}, sock)
            except OSError as e:
                raise lab.LabError(f"the governor is not reachable at {sock} ({e}); start it: ./scripts/benchctl start") from e
            if not r.get("ok"):
                raise lab.LabError(str(r.get("error")))
            if r.get("dry_run"):
                typer.echo("governor in dry-run: nothing was run")
                raise typer.Exit(0)
            if (why := _lab_wait(sock, r.get("job"))):
                raise lab.LabError(why)
            report = json.loads((lab.path(name) / "validate.json").read_text())
    except lab.LabError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(json.dumps(report, indent=2) if output == "json" else lab_validate.text(report))
    raise typer.Exit(1 if report["summary"].startswith("failed") else 0)


@run_app.command("lab")
def run_lab(name: str = typer.Argument(..., help="a lab made with `benchctl import lab`"),
            ab: bool = typer.Option(False, "--ab", help="replay the baseline, restore the snapshot, replay the candidate (default: the candidate only)"),
            target_hours: float = typer.Option(None, "--target-hours", help="run length per side, 0.5 to 6 (default lab.target_hours, 2)"),
            seed: int = typer.Option(None, "--seed", min=0, help="sampling seed, recorded in the run (default 1, the one `get lab` uses)"),
            scenario: str = typer.Option(None, "--scenario", help="play this scenario (scenarios/NAME.yaml, slurm steps on the lab nodes) at the same lab times on both sides; its faults are undone after each side"),
            repeat: int = typer.Option(1, "--repeat", min=1, max=10, help="run the same jobs N times one after the other (same seed); the report then says whether the verdicts agree"),
            prepare: bool = typer.Option(False, "--prepare", hidden=True, help="freeze the jobs and print the run (the governor's child)"),
            run_id: str = typer.Option(None, "--run", hidden=True, help="with --execute: the prepared run"),
            execute: bool = typer.Option(False, "--execute", hidden=True, help="walk a prepared run here (the governor's child)"),
            recover: bool = typer.Option(False, "--recover", hidden=True, help="restore-only: finish a run whose child is gone (the governor's child)"),
            output: str = OUTPUT) -> None:
    """Replay a config lab's sampled trace on the running cluster through the governor (L0-L1, admitted): with --ab the baseline, then
    the candidate from the same snapshot; without it the candidate only. --scenario S plays a scenario's Slurm faults on the lab nodes at
    the same lab times on both sides (its expectations are reported, never the verdict). Returns once the run started; `benchctl status` shows it,
    `benchctl stop lab NAME` ends it. L2 pauses it, L3 aborts it."""
    from .. import lab_run, lab_scenario, lab_steps
    if target_hours is not None and not 0.5 <= target_hours <= 6:
        typer.echo(f"--target-hours must be from 0.5 to 6, not {target_hours:g}", err=True)
        raise typer.Exit(1)
    try:
        if prepare:
            p = lab_steps.prepare(name, _base_sim(), ab, target_hours, lab_run.DEFAULT_SEED if seed is None else seed, scenario=scenario)
            typer.echo(json.dumps(p) if output == "json" else f"run {p['run']} of lab {name}: {p['jobs']} jobs, {p['lab_nodes']} lab nodes")
            return
        if recover:
            if not run_id:
                raise lab.LabError("--recover needs --run RID")
            sock = os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET)
            run = lab_steps.recover(name, run_id, lambda r: checks.governor_request(r, sock, timeout=5))
            raise typer.Exit(0 if run.r.get("restored_after_abort") is not False else 1)
        if execute:
            if not run_id:
                raise lab.LabError("--execute needs --run RID")
            sock = os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET)
            run = lab_steps.execute(name, run_id, _lab_env(), lambda r: checks.governor_request(r, sock, timeout=5))
            raise typer.Exit(0 if run.state == "done" else 1)
        lab.valid_name(name)
        if scenario is not None and not lab_scenario.NAME.fullmatch(scenario):
            raise lab.LabError(f"--scenario must be a scenario name such as drain-mid-job (see: benchctl scenario list), not {scenario!r}")
    except lab.LabError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    req = {"cmd": "lab-run", "name": name, "ab": ab, **({"target_hours": target_hours} if target_hours is not None else {}),
           **({"seed": seed} if seed is not None else {}), **({"scenario": scenario} if scenario is not None else {})}
    for n in range(1, repeat + 1):
        if repeat > 1:
            typer.echo(f"repeat {n} of {repeat}")
        if _lab_request(req, lambda r: f"lab {name}: preparing the run (freezing the jobs, then the memory check)").get("dry_run"):
            return
        rc = _lab_run_outcome(name)
        if rc or n == repeat:
            raise typer.Exit(rc)
        if (state := _wait_run_end(name)) != "done":
            typer.echo(f"repeat {n} of {repeat} ended {state}: not starting the next one", err=True)
            raise typer.Exit(1)


def _wait_run_end(name: str, poll: float = 15.0, sleep: Callable[[float], None] = time.sleep) -> str:
    """Block until the newest run of the lab is done or aborted and return that state (what `run lab --repeat` waits for between runs).
    Reads the run's own record, so it needs no governor answer."""
    from .. import lab_run
    while True:
        runs = sorted(p for p in lab_run.reports_dir(name).glob("*") if p.is_dir())
        state = json.loads((runs[-1] / "run.json").read_text()).get("state") if runs and (runs[-1] / "run.json").is_file() else None
        if state in lab_run.TERMINAL:
            return state
        sleep(poll)


def _lab_run_outcome(name: str, timeout: float = 120) -> int:
    """`lab-run` answers at once (W-13); watch `status` until the run has started or was refused, and say which in one line."""
    sock = os.environ.get("WORKBENCH_GOV_SOCKET", checks.GOV_SOCKET)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            lb = checks.governor_request({"cmd": "status"}, sock).get("lab") or {}
        except OSError as e:
            typer.echo(f"the governor stopped answering: {e}", err=True)
            return 1
        if lb.get("name") == name and lb.get("state") == "refused":
            typer.echo(str(lb.get("reason")), err=True)
            return 1
        if lb.get("name") == name and lb.get("run") and lb.get("state") not in (None, "preparing"):
            typer.echo(f"run {lb['run']} of lab {name} started ({'A/B' if lb.get('ab') else 'candidate only'}, admitted {lb.get('admit_mb')} MB); "
                       f"follow it with `benchctl status`, end it with `benchctl stop lab {name}`")
            return 0
        time.sleep(1)
    typer.echo(f"the run of lab {name} is still preparing after {int(timeout)} s; `benchctl status` shows when it starts", err=True)
    return 1


import_app = typer.Typer(cls=SuggestingGroup, help="Bring exported files in: a production cluster's configuration (config lab)")


@import_app.command("lab")
def import_lab(name: str = typer.Argument(..., help="lab name: lowercase letters, digits and -"),
               src: str = typer.Option(..., "--from", help="folder of exported files (slurm.conf, topology.conf, gres.conf, sacctmgr.dump, qos.txt, trace.sacct, k8s/, rules/, teleport/, versions.yaml)"),
               replace: bool = typer.Option(False, "--replace", help="overwrite a lab of the same name")) -> None:
    """Import exported production configuration into a config lab: parse, scrub secrets, record versions. Reads files only; never connects to production."""
    if replace and (run := _active_lab_run()):
        typer.echo(f"refused: lab run {run.get('run') or '(preparing)'} of {run.get('name')} is {run.get('state')}: "
                   "`import lab --replace` waits until it has ended (the run reads the imported files)", err=True)
        raise typer.Exit(1)
    try:
        m = lab.import_lab(name, _Path(src), replace=replace, sim=_base_sim())
    except lab.LabError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(lab.summary(m))


diff_app = typer.Typer(cls=SuggestingGroup, help="Compare: a config lab's candidate against its baseline")


@add_app.command("candidate")
def add_candidate(name: str = typer.Argument(..., help="a lab made with `benchctl import lab`"),
                  replace: bool = typer.Option(False, "--replace", help="start again from the baseline, dropping your edits")) -> None:
    """Copy the lab's baseline into compose/generated/lab/NAME/candidate/ for editing; `diff lab` and `apply lab --side candidate` read it."""
    try:
        files = lab_candidate.init(name, replace=replace)
    except lab.LabError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(f"candidate of lab {name}: {len(files)} file(s) in compose/generated/lab/{name}/candidate; edit them, then `benchctl diff lab {name}`")


@diff_app.command("lab")
def diff_lab(name: str = typer.Argument(..., help="a lab made with `benchctl import lab`"), output: str = OUTPUT) -> None:
    """The candidate against the baseline, one note per change (live, restart, not applied, not tested), and whether apply restarts slurmctld."""
    try:
        entries = lab_diff.diff(name)
    except lab.LabError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(json.dumps({"name": name, "changes": entries, "restart_keys": lab_diff.restart_keys(entries)}, indent=2) if output == "json" else lab_diff.text(name, entries))


@remove_app.command("lab")
def remove_lab(name: str = typer.Argument(..., help="the lab that is applied"),
               execute: bool = typer.Option(False, "--execute", hidden=True, help="run here, not through the governor (the governor's own child)")) -> None:
    """Return to the plain sim.yaml cluster through the governor: restores the pre-lab accounting, deletes what the lab made, puts slurm.conf back. The imported lab stays on disk."""
    _lab_run("remove", name, "baseline", execute)
