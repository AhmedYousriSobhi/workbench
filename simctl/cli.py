import typer

from . import (
    checks,
    compose,
    describe,
    diag,
    export,
    inventory,
    machine,
    measure,
    nodes,
    scenario,
    stack,
)

app = typer.Typer(help="benchctl: control the simulated cluster. Pattern: benchctl VERB [RESOURCE] [NAME]. "
                       "Start with `benchctl start`, look with `benchctl get nodes`, stop with `benchctl stop`.")
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
    """Show governor level, signals and recent audit entries, then the cluster and the web page."""
    try:
        st = checks.governor_request({"cmd": "status"}, socket)
    except OSError as e:
        typer.echo(f"governor not reachable at {socket}: {e}")
        _cluster_and_page()
        raise typer.Exit(1) from e
    gb = (st["mem_available"] or 0) / checks.GB
    typer.echo(f"level L{st['level']}{' (dry-run)' if st['dry_run'] else ''}  [{st.get('mode', 'root')} mode]  "
               f"avail {gb:.2f} GB  psi some {st['psi_some']}% full {st['psi_full']}%")
    typer.echo(f"groups: {', '.join(st['groups']) or '-'}")
    for v in st.get("violations", []):
        typer.echo(f"  SECURITY: {v}")
    for line in st["audit"]:
        typer.echo(f"  {line}")
    _cluster_and_page()


def _cluster_and_page() -> None:
    """The two lines `benchctl start` users look for: how many containers run, and where the web page is."""
    from pathlib import Path

    running = [r for r in inventory.service_rows() if r["state"] == "running"]
    typer.echo(f"cluster: {len(running)} containers running" if running else "cluster: stopped (benchctl start)")
    log = Path("compose/generated/ui.log")
    link = next((ln.split("opti-sim UI: ", 1)[1].strip() for ln in reversed(log.read_text().splitlines())
                 if "opti-sim UI: " in ln), "") if log.is_file() else ""
    typer.echo(f"web page: {link}" if link and Path("compose/generated/ui.pid").is_file() else "web page: stopped")


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


def _launcher(*args: str) -> None:
    """The one-command launcher (machine check, venv, governor, cluster, web page) lives in scripts/opti."""
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "opti"
    raise typer.Exit(subprocess.run([str(script), *args], check=False).returncode)


@app.command()
def start(profile: str = typer.Option("min", "--profile")) -> None:
    """Everything in one step: machine check, governor, cluster, web page (the first run builds images)."""
    _launcher("start", "--profile", profile)


@app.command()
def stop() -> None:
    """Stop the web page, the cluster and the governor this tool started."""
    _launcher("stop")


@app.command("open")
def open_ui() -> None:
    """Print the web page's login link (and open it in a browser when there is one)."""
    _launcher("open")


@app.command()
def down() -> None:
    """Stop and remove the hpcsim compose project (nothing else)."""
    raise typer.Exit(0 if stack.down(say=typer.echo) else 1)


def _node_names(incomplete: str) -> list[str]:
    """Tab completion for node arguments: every node sim.yaml defines."""
    try:
        return [n for n, _ in nodes.node_names(_sim()) if n.startswith(incomplete)]
    except (OSError, KeyError):
        return []


NODE = typer.Argument(..., autocompletion=_node_names, help="node name, e.g. cn001 or gpu001")
OUTPUT = typer.Option("table", "-o", "--output", help="table or json")

get_app = typer.Typer(help="List what exists: nodes, services, profiles, scenarios")
app.add_typer(get_app, name="get")


@get_app.command("nodes")
def get_nodes(output: str = OUTPUT) -> None:
    """Every node sim.yaml defines, its kind and container state (absent = not started)."""
    typer.echo(inventory.render(inventory.node_rows(_sim()), output))


@get_app.command("services")
def get_services(output: str = OUTPUT) -> None:
    """Every container of the running stack with its state."""
    typer.echo(inventory.render(inventory.service_rows(), output))


@get_app.command("profiles")
def get_profiles(output: str = OUTPUT) -> None:
    """The size presets for `benchctl up --profile`."""
    typer.echo(inventory.render(inventory.profile_rows(), output))


@get_app.command("scenarios")
def get_scenarios(output: str = OUTPUT) -> None:
    """The fault and workload scenarios for `benchctl scenario run`."""
    typer.echo(inventory.render(inventory.scenario_rows(), output))


@app.command("exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def exec_cmd(ctx: typer.Context, node: str = NODE,
             user: str = typer.Option(None, "-u", "--user", help="run as this simulated user, e.g. alice")) -> None:
    """Run a command on a node, or open a shell: `benchctl exec gpu001 -- nvidia-smi`, `benchctl exec cn001`."""
    import os
    import sys

    cmd = ctx.args or ["bash"]
    tty = ["-it"] if sys.stdin.isatty() and not ctx.args else []
    os.execvp("docker", ["docker", "exec", *tty, *(["-u", user] if user else []), f"{stack.PROJECT}-{node}-1", *cmd])


@app.command()
def logs(node: str = NODE, follow: bool = typer.Option(False, "-f", "--follow"),
         tail: int = typer.Option(50, "--tail")) -> None:
    """Container log of a node or service."""
    import os

    os.execvp("docker", ["docker", "logs", *(["-f"] if follow else []), "--tail", str(tail),
                         f"{stack.PROJECT}-{node}-1"])


describe_app = typer.Typer(help="Everything known about one thing")
app.add_typer(describe_app, name="describe")


@describe_app.command("node")
def describe_node(node: str = NODE, output: str = OUTPUT) -> None:
    """Container, Slurm state and jobs, GPUs (busy/idle), BMC power and temperature, how to ssh in."""
    import json

    try:
        info = describe.gather(_sim(), node, describe.http_get, stack._run)
    except describe.UnknownNode as e:
        typer.echo(str(e.args[0]))
        raise typer.Exit(1) from e
    typer.echo(json.dumps(info, indent=2) if output == "json" else describe.render(info))


export_app = typer.Typer(help="Write the workbench in a form another tool can import")
app.add_typer(export_app, name="export")


@export_app.command("profile")
def export_profile(out: str = typer.Option("bench-profile.json", "-o", "--output", help="file to write, or - for stdout"),
                   profile: str = typer.Option(None, "--profile", help="default: the profile `up` last started"),
                   name: str = typer.Option("workbench", "--name", help="the cluster's name in the client"),
                   user: str = typer.Option(None, "--user", help="SSH user, default the first user in sim.yaml"),
                   bmc: bool = typer.Option(False, "--bmc", help="also list one BMC target per node"),
                   fmt: str = typer.Option(None, "--format", help="client format ID[:VERSION]; else $BENCH_EXPORT_FORMAT "
                                                                 "or .git/export-format")) -> None:
    """Write a profile file the monitoring client can import: SSH, Slurm, Grafana, Jira, storage (no secrets)."""
    from pathlib import Path

    sim = _sim()
    prof = profile or stack.current_profile()
    try:
        text = export.to_json(export.build(sim, export.services_for(sim, prof), name, bmc, user), export.format_id(fmt))
    except export.ExportError as e:
        typer.echo(f"cannot export: {e}")
        raise typer.Exit(1) from e
    if out == "-":
        typer.echo(text, nl=False)
        return
    Path(out).write_text(text)
    typer.echo(f"wrote {out} (profile {prof}). Import it in the client, then set what a file never holds:\n"
               "  SSH key: benchctl ssh-key install, then pick ~/.ssh/opti-sim_ed25519\n"
               "  Grafana token: cat compose/generated/secrets/grafana_viewer_token (new after every `up`)"
               + ("\n  BMC password: the part after 'admin:' in compose/generated/secrets/bmc_credentials" if bmc else ""))


ssh_key_app = typer.Typer(help="Put the login key where a client that only reads ~/.ssh can use it")
app.add_typer(ssh_key_app, name="ssh-key")


@ssh_key_app.command("install")
def ssh_key_install() -> None:
    """Copy the login key to ~/.ssh/opti-sim_ed25519 (mode 600) and print the path to give the client."""
    try:
        stack.install_client_key()
    except (OSError, FileNotFoundError) as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    typer.echo(f"key: ~/.ssh/{stack.CLIENT_KEY}  (host 127.0.0.1, port 2222, user alice)\nremove it when done: benchctl ssh-key remove")


@ssh_key_app.command("remove")
def ssh_key_remove() -> None:
    """Delete ~/.ssh/opti-sim_ed25519 (only if it is the opti-sim key)."""
    typer.echo("removed" if stack.remove_client_key() else "nothing to remove")


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
