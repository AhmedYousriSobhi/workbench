import typer

from . import checks, compose, diag, machine, measure, scenario, stack

app = typer.Typer(help="opti-sim control CLI")
ICON = {checks.OK: "ok  ", checks.WARN: "WARN", checks.FAIL: "FAIL"}


@app.command()
def check() -> None:
    """Initial machine check before building (OS, RAM, disk, Docker, ports); exits 1 if not ready."""
    results = machine.run_all()
    typer.echo(machine.report(results))
    raise typer.Exit(1 if any(c.status == machine.FAIL for c in results) else 0)


@app.command()
def doctor() -> None:
    """Check host prerequisites (SPEC P-1..P-3); exits 1 on any failure."""
    results = checks.run_all()
    for c in results:
        typer.echo(f"[{ICON[c.status]}] {c.name}: {c.detail}")
        if c.fix and c.status != checks.OK:
            typer.echo(f"       fix: {c.fix}")
    raise typer.Exit(1 if any(c.status == checks.FAIL for c in results) else 0)


@app.command()
def preflight(profile: str = typer.Option("std", "--profile")) -> None:
    """Would this profile fit in current free memory? (SPEC section 6)"""
    ok, msg = checks.preflight(profile, checks.load_profiles(), checks.mem_available())
    typer.echo(("OK: " if ok else "REFUSED: ") + msg)
    raise typer.Exit(0 if ok else 1)


@app.command()
def status(socket: str = checks.GOV_SOCKET) -> None:
    """Show governor level, signals and recent audit entries."""
    try:
        st = checks.governor_request({"cmd": "status"}, socket)
    except OSError as e:
        typer.echo(f"governor not reachable at {socket}: {e}")
        raise typer.Exit(1) from e
    gb = (st["mem_available"] or 0) / checks.GB
    typer.echo(f"level L{st['level']}{' (dry-run)' if st['dry_run'] else ''}  [{st.get('mode', 'root')} mode]  "
               f"avail {gb:.2f} GB  psi some {st['psi_some']}% full {st['psi_full']}%")
    typer.echo(f"groups: {', '.join(st['groups']) or '-'}")
    for v in st.get("violations", []):
        typer.echo(f"  SECURITY: {v}")
    for line in st["audit"]:
        typer.echo(f"  {line}")


@app.command("diag")
def diag_cmd(out: str = typer.Option(None, "--out")) -> None:
    """Write one tar.gz with host, Docker, libvirt and governor state (secrets masked)."""
    from pathlib import Path

    typer.echo(str(diag.write_bundle(Path(out) if out else None)))


@app.command("compose")
def compose_cmd(profile: str = typer.Option("std", "--profile")) -> None:
    """Generate compose/generated/docker-compose.yml from sim.yaml and the profile."""
    from pathlib import Path

    import yaml

    sim = yaml.safe_load(Path("sim.yaml").read_text())
    prof = compose.load_profile(profile)
    data = compose.generate(sim, prof)
    typer.echo(f"{compose.write(data, sim=sim, compute_nodes=prof.get('compute_nodes'))}: {len(data['services'])} services, "
               f"{compose.budget_mb(data)} MB of limits (plus observability)")


def _sim() -> dict:
    from pathlib import Path

    import yaml

    return yaml.safe_load(Path("sim.yaml").read_text())


@app.command()
def up(profile: str = typer.Option("std", "--profile")) -> None:
    """Pre-flight, then start the profile in dependency order (refuses if it does not fit)."""
    raise typer.Exit(0 if stack.up(_sim(), profile, say=typer.echo) else 1)


@app.command()
def down() -> None:
    """Stop and remove the hpcsim compose project (nothing else)."""
    raise typer.Exit(0 if stack.down(say=typer.echo) else 1)


@app.command()
def scale(n: int = typer.Argument(..., min=0, help="compute node count"),
          profile: str = typer.Option(None, "--profile", help="default: the profile `up` last started")) -> None:
    """Change the compute-node count: regenerates slurm.conf, restarts slurmctld, starts/stops slurmd."""
    raise typer.Exit(0 if stack.scale(_sim(), profile or stack.current_profile(), n, say=typer.echo) else 1)


scenario_app = typer.Typer(help="Run named fault and workload scenarios (scenarios/*.yaml)")
app.add_typer(scenario_app, name="scenario")


@scenario_app.command("list")
def scenario_list() -> None:
    """Name, step count and description of every scenario."""
    for sc in scenario.list_all():
        typer.echo(f"{sc['name']:22} {len(sc.get('steps', [])):2} steps  {sc.get('description', '')}")


@scenario_app.command("run")
def scenario_run(name: str = typer.Argument(..., help="see `scenario list`"),
                 speed: float = typer.Option(1.0, help="fast-forward: 10 plays it ten times faster"),
                 dry_run: bool = typer.Option(False, "--dry-run", help="validate and show the timeline only")) -> None:
    """Play a scenario against the running stack; exit 1 if a step or expectation fails."""
    sim = _sim()
    try:
        sc = scenario.load(name)
    except scenario.ScenarioError as e:
        typer.echo(str(e))
        raise typer.Exit(1) from e
    problems = scenario.validate(sc, sim)
    for p in problems:
        typer.echo(f"invalid: {p}")
    if problems:
        raise typer.Exit(1)
    if dry_run:
        for s in scenario.plan(sc):
            typer.echo(f"[{s.at:6.1f}s] {s.target} {s.do}")
        raise typer.Exit(0)
    report = scenario.run(sc, speed=speed, dilation=float(sim.get("time_dilation", 1.0)), say=typer.echo)
    typer.echo("scenario " + ("passed" if report.ok else "FAILED"))
    raise typer.Exit(0 if report.ok else 1)


measure_app = typer.Typer(help="Phase 0: read-only memory measurement")
app.add_typer(measure_app, name="measure")


@measure_app.command("run")
def measure_run(label: str = typer.Argument(..., help="e.g. idle, loaded"),
                duration: float = typer.Option(30, help="seconds to sample"),
                interval: float = typer.Option(5, help="seconds between samples")) -> None:
    """Sample host, containers, processes (PSS) and VMs; save docs/measurements/<label>.json."""
    snaps = measure.sample(duration, interval)
    typer.echo(f"{len(snaps)} samples -> {measure.save(label, snaps)}")


@measure_app.command("report")
def measure_report(idle: str = typer.Argument(..., help="label of the idle run"),
                   loaded: str = typer.Argument(None, help="label of the loaded run")) -> None:
    """Markdown table: idle vs loaded peak with suggested budgets (peak x 1.2)."""
    import json
    from pathlib import Path

    def load(label: str) -> dict:
        return json.loads((Path("docs/measurements") / f"{label}.json").read_text())

    typer.echo(measure.report(load(idle), load(loaded) if loaded else None))


if __name__ == "__main__":
    app()
