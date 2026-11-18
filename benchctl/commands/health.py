"""`benchctl check|doctor|preflight|status|diag|compose`: is the machine ready, what is running."""

import typer

from .. import (
    agentmode,
    checks,
    completion,
    compose,
    contract,
    diag,
    errors,
    inventory,
    jira_real,
    machine,
    tuning,
)
from .base import JSON, OUTPUT, _profile_check
from .groups import app

ICON = {checks.OK: "ok  ", checks.WARN: "WARN", checks.FAIL: "FAIL"}


@app.command()
def check() -> None:
    """Initial machine check before building (OS, RAM, disk, Docker, ports); exits 1 if not ready."""
    results = machine.run_all()
    typer.echo(machine.report(results))
    raise typer.Exit(1 if any(c.status == machine.FAIL for c in results) else 0)


@app.command()
def doctor(output: str = OUTPUT, json_: bool = JSON) -> None:
    """Check host prerequisites (SPEC P-1..P-3); exits 1 on any failure."""
    results = checks.run_all()
    tune = tuning.plan()
    failed = any(c.status == checks.FAIL for c in results)
    if contract.wanted(output, json_) == "json":
        typer.echo(contract.dumps({"ok": not failed, "checks": [{"name": c.name, "code": errors.doctor_code(c.name), "status": c.status, "detail": c.detail, "fix": c.fix} for c in results],
                                   "tuning": [tuning.as_dict(t) for t in tune]}))
        raise typer.Exit(1 if failed else 0)
    if agentmode.agent():  # only what needs attention, one coded line each
        for c in results:
            if c.status != checks.OK:
                typer.echo(f"{errors.doctor_code(c.name)} {c.status}: {' '.join(c.detail.split())}" + (f" | fix: {c.fix}" if c.fix else ""))
        if not any(c.status != checks.OK for c in results):
            typer.echo("ok: every check passed")
        todo = [t.setting for t in tune if not t.applied]
        if todo:  # one line; the commands are in `benchctl doctor` (text) and `doctor --json` (tuning)
            typer.echo(f"tuning: {', '.join(todo)} not as recommended (print-only; commands in `benchctl doctor --json`)")
        raise typer.Exit(1 if failed else 0)
    for c in results:
        typer.echo(f"[{ICON[c.status]}] {c.name}: {c.detail}")
        if c.fix and c.status != checks.OK:
            typer.echo(f"       fix: {c.fix}")
    block = tuning.render(tune)
    if block:
        typer.echo(block)
    raise typer.Exit(1 if failed else 0)


@app.command()
def preflight(profile: str = typer.Option("std", "--profile", autocompletion=completion.profiles, callback=_profile_check)) -> None:
    """Would this profile fit in current free memory? (SPEC section 6)"""
    ok, msg = checks.preflight(profile, checks.load_profiles(), checks.mem_available())
    typer.echo(("OK: " + msg) if ok else errors.render("WB-REFUSED-MEMORY", msg, "REFUSED: " + msg), err=not ok)
    raise typer.Exit(0 if ok else 1)


@app.command()
def status(socket: str = checks.GOV_SOCKET, output: str = OUTPUT, json_: bool = JSON) -> None:
    """Show governor level, signals and recent audit entries, then the cluster and the web page."""
    as_json = contract.wanted(output, json_) == "json"
    try:
        st = checks.governor_request({"cmd": "status"}, socket)
    except OSError as e:
        if as_json:
            typer.echo(contract.dumps({"governor": {"reachable": False, "socket": socket}, **_cluster_and_page(True)}))
        else:
            typer.echo(f"governor not reachable at {socket}: {e}", err=True)
            _cluster_and_page()
        raise typer.Exit(1) from e
    if as_json:
        typer.echo(contract.dumps({"governor": {
            "reachable": True, "level": st["level"], "dry_run": bool(st["dry_run"]), "mode": st.get("mode", "root"),
            "mem_available_mb": round((st["mem_available"] or 0) / (1024 * 1024)), "psi_some": st["psi_some"], "psi_full": st["psi_full"],
            "groups": list(st["groups"]), "violations": list(st.get("violations", [])), "audit": list(st["audit"])}, **_cluster_and_page(True)}))
        return
    gb = (st["mem_available"] or 0) / checks.GB
    typer.echo(f"level L{st['level']}{' (dry-run)' if st['dry_run'] else ''}  [{st.get('mode', 'root')} mode]  "
               f"avail {gb:.2f} GB  psi some {st['psi_some']}% full {st['psi_full']}%")
    typer.echo(f"groups: {', '.join(st['groups']) or '-'}")
    if st.get("lab"):
        typer.echo(_lab_line(st["lab"]))
    for v in st.get("violations", []):
        typer.echo(f"  SECURITY: {v}")
    for line in st["audit"]:
        typer.echo(f"  {line}")
    _cluster_and_page()


def _lab_line(lb: dict) -> str:
    """The governor's `lab` block as one line: the applied lab, and a run's progress while one is supervised (issue 554)."""
    text = f"lab {lb.get('name')}" + (f" ({lb['side']}) applied" if lb.get("side") else "")
    if lb.get("state") in ("preparing", "refused") and not lb.get("run"):
        return text + f"; run {lb['state']}" + (f": {lb['reason']}" if lb.get("reason") else "")
    if lb.get("run"):
        text += (f"; run {lb['run']}: {lb.get('state')} {lb.get('percent') or 0}%"
                 + (f" side {lb['replay_side']}" if lb.get("replay_side") else "")
                 + f", {lb.get('submitted') or 0} submitted, {lb.get('running') or 0} running, {lb.get('pending') or 0} pending"
                 + (f", paused {lb.get('paused_s') or 0:g} s" if lb.get("paused_s") else "") + (" (PAUSED)" if lb.get("paused") else "")
                 + (" (restore waits for L0-L1)" if lb.get("restore_pending") else "") + (f": {lb['reason']}" if lb.get("reason") else ""))
    return text


def _cluster_and_page(as_data: bool = False) -> dict | None:
    """The two lines `benchctl start` users look for: how many containers run, and where the web page is (`as_data`: the same as a dict)."""
    from pathlib import Path

    running = [r for r in inventory.service_rows() if r["state"] == "running"]
    log = Path("compose/generated/ui.log")
    link = next((ln.split("workbench UI: ", 1)[1].strip() for ln in reversed(log.read_text().splitlines())
                 if "workbench UI: " in ln), "") if log.is_file() else ""
    page = link if link and Path("compose/generated/ui.pid").is_file() else ""
    if as_data:
        return {"cluster": {"running": len(running), "start": None if running else "./scripts/benchctl start"}, "web_page": {"running": bool(page), "url": page or None}}
    typer.echo(f"cluster: {len(running)} containers running" if running else "cluster: stopped (benchctl start)")
    typer.echo(f"web page: {page}" if page else "web page: stopped")
    if any(r["name"] == "jira-real" for r in running):
        typer.echo(f"jira (real): {jira_real.licence_text(jira_real.licence_status())}")


@app.command("diag")
def diag_cmd(out: str = typer.Option(None, "--out")) -> None:
    """Write one tar.gz with host, Docker, libvirt and governor state (secrets masked)."""
    from pathlib import Path

    typer.echo(str(diag.write_bundle(Path(out) if out else None)))


@app.command("compose")
def compose_cmd(profile: str = typer.Option("std", "--profile", autocompletion=completion.profiles, callback=_profile_check)) -> None:
    """Generate compose/generated/docker-compose.yml from sim.yaml and the profile."""
    from pathlib import Path

    import yaml

    sim = yaml.safe_load(Path("sim.yaml").read_text())
    prof = compose.load_profile(profile)
    data = compose.generate(sim, prof)
    typer.echo(f"{compose.write(data, sim=sim, compute_nodes=prof.get('compute_nodes'))}: {len(data['services'])} services, "
               f"{compose.budget_mb(data)} MB of limits (plus observability)")
