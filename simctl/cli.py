import difflib
import sys
from pathlib import Path as _Path

import click
import typer
from typer.core import TyperGroup

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
    workbench,
)


class SuggestingGroup(TyperGroup):
    """A mistyped verb gets a hint and an error (exit 2). It is never guessed and run: `benchctl dwon` must not
    run `down` (CLI guidelines: suggest, do not silently correct; no prefix abbreviations that block future verbs)."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as e:
            close = difflib.get_close_matches(args[0], self.list_commands(ctx), n=1) if args else []
            if close:
                raise click.UsageError(f"{e.message} Did you mean '{close[0]}'?", ctx=ctx) from e
            raise


def _old_name_notice() -> None:
    """One line on stderr, only in a terminal, when the pre-0.2 name is used (scripts stay quiet and keep working)."""
    if _Path(sys.argv[0]).name == "simctl" and sys.stderr.isatty():
        typer.echo("note: `simctl` is now `benchctl` (the old name still works for now).", err=True)


app = typer.Typer(cls=SuggestingGroup, epilog="Scripts: use -o json where offered, read data from stdout (messages and errors "
                  "go to stderr). Exit codes: 0 ok, 1 failed or refused, 2 bad usage.", help="benchctl: control the simulated cluster. Pattern: benchctl VERB [RESOURCE] [NAME]. "
                       "Start with `benchctl start`, look with `benchctl get nodes`, stop with `benchctl stop`.")
@app.callback()
def _main() -> None:
    _old_name_notice()


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
    typer.echo(("OK: " if ok else "REFUSED: ") + msg, err=not ok)
    raise typer.Exit(0 if ok else 1)


@app.command()
def status(socket: str = checks.GOV_SOCKET) -> None:
    """Show governor level, signals and recent audit entries, then the cluster and the web page."""
    try:
        st = checks.governor_request({"cmd": "status"}, socket)
    except OSError as e:
        typer.echo(f"governor not reachable at {socket}: {e}", err=True)
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
    link = next((ln.split("workbench UI: ", 1)[1].strip() for ln in reversed(log.read_text().splitlines())
                 if "workbench UI: " in ln), "") if log.is_file() else ""
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


def _base_sim() -> dict:
    from pathlib import Path

    import yaml

    return yaml.safe_load(Path("sim.yaml").read_text())


def _sim() -> dict:
    """sim.yaml with the workbench last built applied (node counts), so every command sees what is really there."""
    base = _base_sim()
    try:
        spec = workbench.current(base)
    except workbench.SpecError:
        return base
    return workbench.effective(base, spec)[0] if spec else base


def _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes):
    """(spec, sim, profile) from a workbench file and/or flags; flags win. Exits 1 with every problem listed."""
    base = _base_sim()
    try:
        spec = workbench.load_file(file, workbench.defaults(base)) if file else workbench.defaults(base)
    except workbench.SpecError as e:
        typer.echo(f"invalid workbench: {e}", err=True)
        raise typer.Exit(1) from e
    for key, val in (("name", name), ("cpu_nodes", cpu_nodes), ("gpu_nodes", gpu_nodes),
                     ("gpus_per_node", gpus_per_node), ("k8s_nodes", k8s_nodes)):
        if val is not None:
            setattr(spec, key, val)
    spec.parts = sorted((set(spec.parts) | set(with_ or [])) - set(without or []))
    if problems := spec.problems():
        for p in problems:
            typer.echo(f"invalid: {p}", err=True)
        raise typer.Exit(1)
    return (spec, *workbench.effective(base, spec))


FILE = typer.Option(None, "-f", "--file", help="workbench file (YAML: cpu_nodes, gpu_nodes, gpus_per_node, parts)")
NAME = typer.Option(None, "--name", help="workbench name")
CPU = typer.Option(None, "--cpu-nodes", help="compute nodes (default: sim.yaml)")
GPU = typer.Option(None, "--gpu-nodes", help="GPU nodes (default: sim.yaml)")
GPUS = typer.Option(None, "--gpus-per-node", help="GPUs on each GPU node, 1 to 8")
WITH = typer.Option(None, "--with", help=f"add a part: {', '.join(workbench.PARTS)} (repeat)")
WITHOUT = typer.Option(None, "--without", help="remove a part (repeat)")
K8S = typer.Option(None, "--k8s-nodes", help="Kubernetes nodes (not built yet)")
SAVE = typer.Option(None, "--save", help="write the selection to this workbench file")


@app.command()
def plan(file: str = FILE, name: str = NAME, cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS,
         with_: list[str] = WITH, without: list[str] = WITHOUT, k8s_nodes: int = K8S, save: str = SAVE) -> None:
    """Estimate the memory a workbench needs against what the host has free; exit 1 if it would not fit."""
    from pathlib import Path

    import yaml

    spec, sim, prof = _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes)
    est = workbench.estimate(sim, prof)
    ok, msg = workbench.verdict(est, checks.mem_available() // (1024 * 1024), spec=spec)
    typer.echo(workbench.render(spec, est) + f"\n\n{msg}")
    if save:
        Path(save).write_text(yaml.safe_dump(spec.to_dict(), sort_keys=False))
        typer.echo(f"saved {save}; build it with: benchctl apply -f {save}")
    raise typer.Exit(0 if ok else 1)


def _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes) -> None:
    spec, sim, prof = _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes)
    typer.echo(workbench.render(spec, workbench.estimate(sim, prof)) + "\n")
    if not stack.up(sim, "workbench", say=typer.echo, prof=prof):
        raise typer.Exit(1)
    workbench.save(spec)


@app.command()
def up(profile: str = typer.Option(None, "--profile", help="a size preset (default std); or choose by counts below"),
       file: str = FILE, name: str = NAME, cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS,
       with_: list[str] = WITH, without: list[str] = WITHOUT, k8s_nodes: int = K8S) -> None:
    """Estimate, then start. By preset (--profile) or by selection (--cpu-nodes 2 --gpu-nodes 4 --with jira ...)."""
    chosen = any(v is not None for v in (file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes))
    if chosen and profile:
        typer.echo("choose one: --profile PRESET, or a selection (--cpu-nodes, --gpu-nodes, --with, -f FILE)", err=True)
        raise typer.Exit(1)
    if chosen:
        _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes)
        return
    workbench.forget()
    raise typer.Exit(0 if stack.up(_base_sim(), profile or "std", say=typer.echo) else 1)


@app.command()
def apply(file: str = typer.Option(..., "-f", "--file", help="workbench file to build"), name: str = NAME,
          cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS, with_: list[str] = WITH,
          without: list[str] = WITHOUT, k8s_nodes: int = K8S) -> None:
    """Build the workbench a file describes (the same as `up -f FILE`)."""
    _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes)


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


def _confirm(what: str, yes: bool) -> None:
    """Ask before a step that ends shells or removes nodes, but only for a person at a terminal: scripts and the
    launcher (no TTY) are never blocked, and `--yes` skips the question."""
    if not yes and sys.stdin.isatty() and not typer.confirm(f"{what} Continue?", default=False):
        typer.echo("cancelled", err=True)
        raise typer.Exit(1)


@app.command()
def down(dry_run: bool = typer.Option(False, "-n", "--dry-run", help="list what would stop; change nothing"),
         yes: bool = typer.Option(False, "-y", "--yes", help="do not ask (a terminal asks; scripts are never asked)")) -> None:
    """Stop and remove the hpcsim compose project (nothing else)."""
    running = [r["name"] for r in inventory.service_rows() if r["state"] == "running"]
    if dry_run:
        typer.echo(f"would stop and remove {len(running)} containers: {', '.join(running) or 'none running'}")
        return
    if running:
        _confirm(f"This stops {len(running)} containers and ends open shells on the nodes.", yes)
    raise typer.Exit(0 if stack.down(say=typer.echo) else 1)


def _node_names(incomplete: str) -> list[str]:
    """Tab completion for node arguments: every node sim.yaml defines."""
    try:
        return [n for n, _ in nodes.node_names(_sim()) if n.startswith(incomplete)]
    except (OSError, KeyError):
        return []


NODE = typer.Argument(..., autocompletion=_node_names, help="node name, e.g. cn001 or gpu001")
OUTPUT = typer.Option("table", "-o", "--output", help="table, json (for scripts) or name (one per line)")
SELECTOR = typer.Option(None, "-l", "--selector", help="keep rows where key=value or key!=value (repeat or comma-separate)")
SORT_BY = typer.Option(None, "--sort-by", help="sort by a column name")
WATCH = typer.Option(False, "-w", "--watch", help="redraw until Ctrl-C")
INTERVAL = typer.Option(2.0, "--interval", help="seconds between redraws with --watch (at least 1)")

get_app = typer.Typer(cls=SuggestingGroup, help="List what exists: nodes, services, profiles, scenarios")
app.add_typer(get_app, name="get")


def _get(rows_fn, output: str, selector: list[str] | None, sort_by: str | None, watch: bool, interval: float) -> None:
    """One list command: query, render, and optionally redraw. Query mistakes exit 2 (usage), like an unknown flag."""
    import time

    def once() -> str:
        return inventory.render(inventory.sort_rows(inventory.select(rows_fn(), selector or []), sort_by), output)

    try:
        if not watch:
            typer.echo(once())
            return
        if interval < 1:
            raise inventory.QueryError("--interval must be at least 1 second (each redraw asks Docker)")
        while True:
            text = once()
            typer.echo(("\033[2J\033[H" if sys.stdout.isatty() else "") + text)
            time.sleep(interval)
    except inventory.QueryError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@get_app.command("nodes")
def get_nodes(output: str = OUTPUT, selector: list[str] = SELECTOR, sort_by: str = SORT_BY, watch: bool = WATCH,
              interval: float = INTERVAL) -> None:
    """Every node sim.yaml defines, its kind and container state (absent = not started). Example: -l kind=gpu."""
    _get(lambda: inventory.node_rows(_sim()), output, selector, sort_by, watch, interval)


@get_app.command("services")
def get_services(output: str = OUTPUT, selector: list[str] = SELECTOR, sort_by: str = SORT_BY, watch: bool = WATCH,
                 interval: float = INTERVAL) -> None:
    """Every container of the running stack with its state."""
    _get(inventory.service_rows, output, selector, sort_by, watch, interval)


@get_app.command("workbench")
def get_workbench(output: str = OUTPUT) -> None:
    """The workbench last built by `up`/`apply` with a selection (none after a plain `up --profile`)."""
    import json

    import yaml

    spec = workbench.current(_base_sim())
    if spec is None:
        typer.echo("none: the last start used a size preset (benchctl get profiles)")
        return
    data = spec.to_dict()
    typer.echo(json.dumps(data, indent=2) if output == "json" else yaml.safe_dump(data, sort_keys=False).rstrip())


@get_app.command("profiles")
def get_profiles(output: str = OUTPUT, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """The size presets for `benchctl up --profile`."""
    _get(inventory.profile_rows, output, selector, sort_by, False, 2.0)


@get_app.command("scenarios")
def get_scenarios(output: str = OUTPUT, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """The fault and workload scenarios for `benchctl scenario run`."""
    _get(inventory.scenario_rows, output, selector, sort_by, False, 2.0)


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


describe_app = typer.Typer(cls=SuggestingGroup, help="Everything known about one thing")
app.add_typer(describe_app, name="describe")


@describe_app.command("node")
def describe_node(node: str = NODE, output: str = OUTPUT) -> None:
    """Container, Slurm state and jobs, GPUs (busy/idle), BMC power and temperature, how to ssh in."""
    import json

    try:
        info = describe.gather(_sim(), node, describe.http_get, stack._run)
    except describe.UnknownNode as e:
        typer.echo(str(e.args[0]), err=True)
        raise typer.Exit(1) from e
    typer.echo(json.dumps(info, indent=2) if output == "json" else describe.render(info))


export_app = typer.Typer(cls=SuggestingGroup, help="Write the workbench in a form another tool can import")
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
    spec = workbench.current(_base_sim())
    prof = profile or ("workbench" if spec else stack.current_profile())
    try:
        services = (set(compose.generate(*workbench.effective(_base_sim(), spec))["services"]) | {"grafana"}
                    if spec and not profile else export.services_for(sim, prof))
        text = export.to_json(export.build(sim, services, name, bmc, user), export.format_id(fmt))
    except export.ExportError as e:
        typer.echo(f"cannot export: {e}", err=True)
        raise typer.Exit(1) from e
    if out == "-":
        typer.echo(text, nl=False)
        return
    Path(out).write_text(text)
    typer.echo(f"wrote {out} (profile {prof}). Import it in the client, then set what a file never holds:\n"
               "  SSH key: benchctl ssh-key install, then pick ~/.ssh/workbench_ed25519\n"
               "  Grafana token: cat compose/generated/secrets/grafana_viewer_token (new after every `up`)"
               + ("\n  BMC password: the part after 'admin:' in compose/generated/secrets/bmc_credentials" if bmc else ""))


ssh_key_app = typer.Typer(cls=SuggestingGroup, help="Put the login key where a client that only reads ~/.ssh can use it")
app.add_typer(ssh_key_app, name="ssh-key")


@ssh_key_app.command("install")
def ssh_key_install() -> None:
    """Copy the login key to ~/.ssh/workbench_ed25519 (mode 600) and print the path to give the client."""
    try:
        stack.install_client_key()
    except (OSError, FileNotFoundError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"key: ~/.ssh/{stack.CLIENT_KEY}  (host 127.0.0.1, port 2222, user alice)\nremove it when done: benchctl ssh-key remove")


@ssh_key_app.command("remove")
def ssh_key_remove() -> None:
    """Delete ~/.ssh/workbench_ed25519 (only if it is the workbench key)."""
    typer.echo("removed" if stack.remove_client_key() else "nothing to remove")


@app.command()
def scale(n: int = typer.Argument(..., min=0, help="compute node count"),
          profile: str = typer.Option(None, "--profile", help="default: the profile `up` last started"),
          dry_run: bool = typer.Option(False, "-n", "--dry-run", help="say which nodes start or stop; change nothing"),
          yes: bool = typer.Option(False, "-y", "--yes", help="do not ask before removing nodes (a terminal asks)")) -> None:
    """Change the compute-node count: regenerates slurm.conf, restarts slurmctld, starts/stops slurmd."""
    if workbench.current(_base_sim()):
        typer.echo("a workbench is built: to change its size run `benchctl up --cpu-nodes N ...` "
                   "(or edit its file and `benchctl apply -f FILE`); `scale` works on a size preset (`up --profile`)")
        raise typer.Exit(1)
    now = stack.running_nodes()
    want = [name for name, kind in nodes.node_names(_sim(), n) if kind == "compute"]
    stop = [x for x in now if x not in want]
    if dry_run:
        typer.echo(f"would start: {', '.join(x for x in want if x not in now) or 'none'}; "
                   f"would stop and remove: {', '.join(stop) or 'none'}")
        return
    if stop:
        _confirm(f"This removes {', '.join(stop)}; shells and jobs on them end.", yes)
    raise typer.Exit(0 if stack.scale(_sim(), profile or stack.current_profile(), n, say=typer.echo) else 1)


scenario_app = typer.Typer(cls=SuggestingGroup, help="Run named fault and workload scenarios (scenarios/*.yaml)")
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
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    problems = scenario.validate(sc, sim)
    for p in problems:
        typer.echo(f"invalid: {p}", err=True)
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
    app(prog_name="benchctl")
