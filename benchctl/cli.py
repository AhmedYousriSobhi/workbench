import difflib
import sys
from pathlib import Path as _Path

import click
import typer
import typer.completion
from typer.core import TyperGroup

from . import (
    checks,
    completion,
    compose,
    describe,
    diag,
    export,
    inventory,
    logins,
    machine,
    measure,
    nhc,
    nodes,
    scenario,
    stack,
    teleport,
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


def _profile_check(value: str | None) -> str | None:
    return completion.check_value(value, completion.profiles(""), "profile")


def _node_check(value: str) -> str:
    return completion.check_value(value, completion.nodes(""), "node")


def _target_check(value: str) -> str:
    return completion.check_value(value, completion.targets(""), "node or service")


typer.completion.completion_init()  # register the shell handlers; the options are ours (`benchctl completion`)
app = typer.Typer(cls=SuggestingGroup, add_completion=False, epilog="Scripts: use -o json where offered, read data from stdout (messages and errors "
                  "go to stderr). Exit codes: 0 ok, 1 failed or refused, 2 bad usage.", help="benchctl: control the simulated cluster. Pattern: benchctl VERB [RESOURCE] [NAME]. "
                       "Start with `benchctl start`, look with `benchctl get nodes`, stop with `benchctl stop`.")
@app.callback()
def _main() -> None:
    pass


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
def preflight(profile: str = typer.Option("std", "--profile", autocompletion=completion.profiles, callback=_profile_check)) -> None:
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
def compose_cmd(profile: str = typer.Option("std", "--profile", autocompletion=completion.profiles, callback=_profile_check)) -> None:
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


def _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, bmc_nodes=None, login=None):
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
    if bmc_nodes is not None:
        spec.bmc_nodes = [n for n in bmc_nodes.split(",") if n]
    if login is not None:
        spec.login_nodes = _login_modes(login)
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
WITH = typer.Option(None, "--with", autocompletion=completion.parts, help=f"add a part: {', '.join(workbench.PARTS)} (repeat)")
WITHOUT = typer.Option(None, "--without", autocompletion=completion.parts, help="remove a part (repeat)")
K8S = typer.Option(None, "--k8s-nodes", help="Kubernetes nodes (not built yet)")
BMC_NODES = typer.Option(None, "--bmc-nodes", help="nodes that get a BMC address, comma separated (default all); each costs about 32 MB")

def _login_modes(login) -> list[str]:
    try:
        return logins.parse(login)
    except logins.LoginError as e:
        typer.echo(f"invalid: {e}", err=True)
        raise typer.Exit(1) from e


LOGIN = typer.Option(None, "--login", autocompletion=completion.login_modes, rich_help_panel="Parts by category",
                     help=f"login nodes, one auth mode each: {', '.join(logins.MODES)} (comma separated), e.g. ssh,teleport; "
                          "the first is `login`, then login2, ...")


def _category_option(cat: str):
    what = workbench.CATEGORIES[cat][0]
    return typer.Option(None, f"--{cat}", autocompletion=completion.category(cat), rich_help_panel="Parts by category",
                        help=f"{what}: {', '.join(workbench.items(cat))} (comma separated or repeated)")


WORKLOAD, ACCOUNTING, MONITORING, TICKETING, HARDWARE, ACCESS, SIMULATION = (
    _category_option(c) for c in ("workload", "accounting", "monitoring", "ticketing", "hardware", "access", "simulation"))
ADDONS = typer.Option(False, "--addons", rich_help_panel="Parts by category",
                      help="turn on every optional part (all categories); --without X still removes one")


def _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, simulation, addons, login=None) -> list[str] | None:
    """`--with` parts plus the ones the category flags and --addons name, or exit 1 with the one problem. None when
    nothing was chosen, so `up` can still tell a plain preset from a selection."""
    picked = {"workload": workload, "accounting": accounting, "monitoring": monitoring, "ticketing": ticketing,
              "hardware": hardware, "access": access, "simulation": simulation}
    try:
        extra = workbench.expand(picked, addons)
    except workbench.SpecError as e:
        typer.echo(f"invalid: {e}", err=True)
        raise typer.Exit(1) from e
    if login is not None and any(m in logins.VIA_TELEPORT for m in _login_modes(login)):
        extra = sorted({*extra, "teleport"})  # a login node that uses Teleport needs it on
    chosen = with_ is not None or addons or any(v is not None for v in picked.values()) or (login is not None and "teleport" in extra)
    return sorted(set(with_ or []) | set(extra)) if chosen else None


SAVE = typer.Option(None, "--save", help="write the selection to this workbench file")


@app.command()
def plan(file: str = FILE, name: str = NAME, cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS,
         with_: list[str] = WITH, without: list[str] = WITHOUT, k8s_nodes: int = K8S, save: str = SAVE,
         bmc_nodes: str = BMC_NODES, workload: list[str] = WORKLOAD, accounting: list[str] = ACCOUNTING,
         monitoring: list[str] = MONITORING, ticketing: list[str] = TICKETING, hardware: list[str] = HARDWARE,
         access: list[str] = ACCESS, simulation: list[str] = SIMULATION, addons: bool = ADDONS, login: list[str] = LOGIN) -> None:
    """Estimate the memory a workbench needs against what the host has free; exit 1 if it would not fit."""
    from pathlib import Path

    import yaml

    with_ = _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, simulation, addons, login)
    spec, sim, prof = _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, bmc_nodes, login)
    est = workbench.estimate(sim, prof)
    ok, msg = workbench.verdict(est, checks.mem_available() // (1024 * 1024), spec=spec)
    typer.echo(workbench.render(spec, est) + f"\n\n{msg}")
    if save:
        Path(save).write_text(yaml.safe_dump(spec.to_dict(), sort_keys=False))
        typer.echo(f"saved {save}; build it with: benchctl apply -f {save}")
    raise typer.Exit(0 if ok else 1)


def _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, bmc_nodes=None, login=None) -> None:
    spec, sim, prof = _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, bmc_nodes, login)
    typer.echo(workbench.render(spec, workbench.estimate(sim, prof)) + "\n")
    if not stack.up(sim, "workbench", say=typer.echo, prof=prof):
        raise typer.Exit(1)
    workbench.save(spec)


@app.command()
def up(profile: str = typer.Option(None, "--profile", autocompletion=completion.profiles, callback=_profile_check, help="a size preset (default std); or choose by counts below"),
       file: str = FILE, name: str = NAME, cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS,
       with_: list[str] = WITH, without: list[str] = WITHOUT, k8s_nodes: int = K8S, bmc_nodes: str = BMC_NODES,
       workload: list[str] = WORKLOAD, accounting: list[str] = ACCOUNTING, monitoring: list[str] = MONITORING,
       ticketing: list[str] = TICKETING, hardware: list[str] = HARDWARE, access: list[str] = ACCESS,
       simulation: list[str] = SIMULATION, addons: bool = ADDONS, login: list[str] = LOGIN) -> None:
    """Estimate, then start. By preset (--profile) or by selection (--cpu-nodes 2 --gpu-nodes 4 --with jira,
    --ticketing jira --hardware bmc,bcm, --addons ...)."""
    with_ = _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, simulation, addons, login)
    chosen = any(v is not None for v in (file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, bmc_nodes, login))
    if chosen and profile:
        typer.echo("choose one: --profile PRESET, or a selection (--cpu-nodes, --gpu-nodes, --with, -f FILE)", err=True)
        raise typer.Exit(1)
    if chosen:
        _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, bmc_nodes, login)
        return
    workbench.forget()
    raise typer.Exit(0 if stack.up(_base_sim(), profile or "std", say=typer.echo) else 1)


@app.command()
def apply(file: str = typer.Option(..., "-f", "--file", help="workbench file to build"), name: str = NAME,
          cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS, with_: list[str] = WITH,
          without: list[str] = WITHOUT, k8s_nodes: int = K8S, bmc_nodes: str = BMC_NODES,
          workload: list[str] = WORKLOAD, accounting: list[str] = ACCOUNTING, monitoring: list[str] = MONITORING,
          ticketing: list[str] = TICKETING, hardware: list[str] = HARDWARE, access: list[str] = ACCESS,
          simulation: list[str] = SIMULATION, addons: bool = ADDONS, login: list[str] = LOGIN) -> None:
    """Build the workbench a file describes (the same as `up -f FILE`)."""
    with_ = _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, simulation, addons, login)
    _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, bmc_nodes, login)


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
          hardware: list[str] = HARDWARE, access: list[str] = ACCESS, simulation: list[str] = SIMULATION,
          addons: bool = ADDONS, login: list[str] = LOGIN) -> None:
    """Everything in one step: machine check, governor, cluster, web page (the first run builds images). Add parts
    with --with (`--with accounting`), by category (`--ticketing jira --hardware bmc,bcm`) or all of them (`--addons`);
    on a running cluster it adds them and leaves the page up."""
    with_ = _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, simulation, addons, login)
    if profile and (with_ is not None or without or login is not None):
        typer.echo("choose one: --profile PRESET, or parts (--with, --without, category flags, --addons, --login)", err=True)
        raise typer.Exit(1)
    if with_ == [] or (with_ is None and login is not None):  # a flag that adds nothing still asks for a selection, not the min preset
        with_ = list(workbench.DEFAULT_PARTS)
    _offer_completion()
    modes = [] if login is None else ["--login", ",".join(_login_modes(login))]
    parts = [x for p in (with_ or []) for x in ("--with", p)] + [x for p in (without or []) for x in ("--without", p)] + modes
    _launcher("start", *(parts or ["--profile", profile or "min"]))


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


completion_app = typer.Typer(cls=SuggestingGroup, help="Tab completion: install, remove, or print the script for bash, zsh, fish")
app.add_typer(completion_app, name="completion")


def _completion_install(shell: str) -> None:
    for line in completion.install(shell, _Path(__file__).resolve().parent.parent):
        typer.echo(line)


def _shell_arg(shell: str | None) -> str:
    shell = shell or completion.detect_shell()
    if shell not in completion.SHELLS:
        typer.echo(f"which shell? Valid: {', '.join(completion.SHELLS)} (e.g. benchctl completion install bash)", err=True)
        raise typer.Exit(2)
    return shell


SHELL = typer.Argument(None, help="bash, zsh or fish (default: the one in $SHELL)")


@completion_app.command("install")
def completion_install(shell: str = SHELL) -> None:
    """Turn on Tab completion for your shell. No sudo; one line in your rc file; undo with `completion remove`."""
    _completion_install(_shell_arg(shell))


@completion_app.command("remove")
def completion_remove(shell: str = SHELL) -> None:
    """Undo `completion install`: the script, the rc line and the ~/.local/bin link."""
    for line in completion.remove(_shell_arg(shell), _Path(__file__).resolve().parent.parent):
        typer.echo(line)


for _sh in completion.SHELLS:
    def _show(_sh: str = _sh) -> None:
        typer.echo(completion.script(_sh), nl=False)
    _show.__doc__ = f"Print the {_sh} completion script (source it yourself, or use `completion install`)."
    completion_app.command(_sh)(_show)


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
    """Stop and remove the workbench compose project (nothing else)."""
    running = [r["name"] for r in inventory.service_rows() if r["state"] == "running"]
    if dry_run:
        typer.echo(f"would stop and remove {len(running)} containers: {', '.join(running) or 'none running'}")
        return
    if running:
        _confirm(f"This stops {len(running)} containers and ends open shells on the nodes.", yes)
    raise typer.Exit(0 if stack.down(say=typer.echo) else 1)


NODE = typer.Argument(..., autocompletion=completion.nodes, callback=_node_check, help="node name, e.g. cn001 or gpu001")
TARGET = typer.Argument(..., autocompletion=completion.targets, callback=_target_check,
                        help="node or service, e.g. cn001, gpu001, slurmctld")
OUTPUT = typer.Option("table", "-o", "--output", autocompletion=completion.outputs, help="table, json (for scripts) or name (one per line)")
SELECTOR = typer.Option(None, "-l", "--selector", autocompletion=completion.selectors, help="keep rows where key=value or key!=value (repeat or comma-separate)")
SORT_BY = typer.Option(None, "--sort-by", autocompletion=completion.columns, help="sort by a column name")
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


@get_app.command("checks")
def get_checks(output: str = OUTPUT, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """The node health checks (NHC) in the order they run, whether each is on, its parameters. Templates: `-o name` lists the checks; see `add check --help`."""
    try:
        _get(inventory.check_rows, output, selector, sort_by, False, 2.0)
    except nhc.NhcError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


@get_app.command("scenarios")
def get_scenarios(output: str = OUTPUT, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """The fault and workload scenarios for `benchctl scenario run`."""
    _get(inventory.scenario_rows, output, selector, sort_by, False, 2.0)


set_app = typer.Typer(cls=SuggestingGroup, help="Change one thing on the running cluster")
app.add_typer(set_app, name="set")


@set_app.command("node")
def set_node(node: str = TARGET, state: str = typer.Argument(..., help="off or on (freeze the node so Slurm detects it by itself), "
                                                                       "down, drain or resume (set the Slurm state by hand)"),
             reason: str = typer.Option(None, "--reason", help="the reason Slurm shows for down or drain")) -> None:
    """`benchctl set node cn001 off` powers the node off like the page's button: the BMC reports it off, the BCM and the GPU metrics
    follow, and its container freezes (slurmd stops answering; slurmctld marks it DOWN* after about 30 s, as for a dead host). `on` reverses it. `down`/`resume` set the Slurm state directly. The page's Power off/on does off/on."""
    keys = {"off": "power_off", "on": "power_on", "down": "down", "drain": "drain", "resume": "resume"}
    if state not in keys:
        typer.echo("state must be off, on, down, drain or resume", err=True)
        raise typer.Exit(1)
    run, post = (lambda c: stack._run(c)), (lambda url, body: scenario.post(url, body))
    slurm_only = scenario.Step(0, "slurm", {keys[state]: [node], "reason": "workbench power off"})
    o = scenario.execute(slurm_only if state not in ("off", "on") else
                         scenario.Step(0, "bmc-farm", {"nodes": [node], "power": "Off" if state == "off" else "On"}),  # the page's Power off/on: BMC, BCM and GPU metrics follow
                         poster=post, host=True, runner=run)
    if o.status != "ok" and state in ("off", "on"):  # no BMC farm in this profile: freeze or thaw alone
        o = scenario.execute(slurm_only, poster=post, host=True, runner=run)
    typer.echo(f"{node}: {state}" if o.status == "ok" else f"cannot set {node}: {o.detail}", err=o.status != "ok")
    raise typer.Exit(0 if o.status == "ok" else 1)


CHECK = typer.Argument(..., autocompletion=completion.checks, help="check name, see `benchctl get checks`")
SETS = typer.Option(None, "--set", help="a parameter, key=value (repeat for several)")
POSITION = typer.Option(None, "--position", help="place in the run order, 1 = first")

add_app = typer.Typer(cls=SuggestingGroup, help="Add something: a health check, a Teleport invite")
app.add_typer(add_app, name="add")
remove_app = typer.Typer(cls=SuggestingGroup, help="Remove something: a health check")
app.add_typer(remove_app, name="remove")
validate_app = typer.Typer(cls=SuggestingGroup, help="Check files without changing anything: the health checks")
app.add_typer(validate_app, name="validate")
reload_app = typer.Typer(cls=SuggestingGroup, help="Push edited files to the running nodes: the health checks")
app.add_typer(reload_app, name="reload")


def _nhc_done(what: str) -> None:
    """The change is in the files; deliver it to the nodes' mounted copy and say so (a node reads it at its next run)."""
    if errs := nhc.sync():
        typer.echo("\n".join(errs), err=True)
        raise typer.Exit(1)
    typer.echo(f"{what}. Running nodes use it at their next health check (within 30 s); `benchctl get checks` shows the order.")


def _nhc_run(fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except nhc.NhcError as e:
        typer.echo(f"refused, nothing changed:\n{e}", err=True)
        raise typer.Exit(1) from e


@add_app.command("check")
def add_check(name: str = typer.Argument(..., help="new check name: lower case letters, digits, '-'"),
              template: str = typer.Option(None, "--template", autocompletion=completion.check_templates, help="a builtin template, e.g. metric-above"),
              script: str = typer.Option(None, "--script", help="a shell file with your own check (prints one line per problem)"),
              sets: list[str] = SETS, position: int = POSITION) -> None:
    """Add a health check. From a template: `benchctl add check gpu-hot --template metric-above --set metric=DCGM_FI_DEV_GPU_TEMP --set max=85`.
    Your own: `benchctl add check my-check --script ./my-check.sh` (the file prints one line per problem, nothing when healthy). The
    new check is validated first, including a run on healthy sample metrics, and refused when it fails them."""
    if template is None and script is None:
        typer.echo("give --template NAME (see the names with `ls images/node-base/nhc.d/templates`) or --script FILE", err=True)
        raise typer.Exit(2)
    if template is not None and script is not None:
        typer.echo("give --template or --script, not both", err=True)
        raise typer.Exit(2)

    def go() -> None:
        body = _Path(script).read_text() if script else None
        nhc.add(name, template=template, script=body, params=nhc.parse_sets(sets), position=position)

    try:
        _nhc_run(go)
    except OSError as e:
        typer.echo(f"cannot read {script}: {e.strerror}", err=True)
        raise typer.Exit(1) from e
    _nhc_done(f"added check {name}")


@add_app.command("invite")
def add_invite(user: str = typer.Argument(..., help="a user from sim.yaml")) -> None:
    """Make a one-time link to choose USER's Teleport password, for `tsh login`: `benchctl add invite alice`. Open it in a
    browser once (the page is served with a self-signed certificate). Needs Teleport running (`--with teleport`)."""
    if user not in [u["name"] for u in _sim().get("users", [])]:
        typer.echo(f"{user!r} is not a user in sim.yaml", err=True)
        raise typer.Exit(2)
    try:
        typer.echo(teleport.invite(user, stack._run, stack.dc))
    except teleport.TeleportError as e:
        typer.echo(f"{e}\nIs Teleport running? Start it with `benchctl up --with teleport` (see `benchctl get services`).", err=True)
        raise typer.Exit(1) from e


@set_app.command("check")
def set_check(name: str = CHECK, state: str = typer.Argument(None, help="on or off"), sets: list[str] = SETS, position: int = POSITION) -> None:
    """Switch a check on or off, change its parameters, or move it: `benchctl set check gpu-xid off`, `... set check gpu-hot --set max=90`, `... --position 1`."""
    if state not in (None, "on", "off"):
        typer.echo("state must be on or off", err=True)
        raise typer.Exit(2)
    if state is None and not sets and position is None:
        typer.echo("nothing to change: give on|off, --set key=value or --position N", err=True)
        raise typer.Exit(2)
    _nhc_run(lambda: nhc.set_check(name, enabled=None if state is None else state == "on", params=nhc.parse_sets(sets), position=position))
    _nhc_done(f"changed check {name}")


@remove_app.command("check")
def remove_check(name: str = CHECK) -> None:
    """Remove a check and its file from images/node-base/nhc.d (git keeps the old one). To keep it, `set check NAME off` instead."""
    _nhc_run(nhc.remove, name)
    _nhc_done(f"removed check {name}")


@validate_app.command("checks")
def validate_checks() -> None:
    """Validate every check file and checks.conf (grammar, parameters, bash syntax, the contract, a run on healthy sample metrics). Changes nothing."""
    errs = nhc.problems()
    if errs:
        typer.echo("\n".join(errs), err=True)
        raise typer.Exit(1)
    typer.echo(f"ok: {len(nhc.load())} checks valid")


@reload_app.command("checks")
def reload_checks() -> None:
    """After editing a check file or images/node-base/nhc.d/checks.conf by hand: validate, then push to the running nodes. Nothing is pushed when a check is invalid."""
    _nhc_done("validated and delivered")


@app.command("submit")
def submit(node: str = TARGET, gpus: int = typer.Option(0, "--gpus", help="GPUs the job holds (GPU nodes)"),
           cpus: int = typer.Option(1, "--cpus", help="CPUs the job holds"),
           duration: int = typer.Option(300, "--duration", help="seconds the job runs (10 to 3600)"),
           user: str = typer.Option(None, "-u", "--user", autocompletion=completion.users, help="simulated user, default the first in sim.yaml")) -> None:
    """Submit a job on one node: `benchctl submit gpu001 --gpus 4 --duration 600`. It sleeps for the duration, holding its CPUs
    and GPUs, so squeue lists it, the node shows allocated and the GPU metrics show those GPUs busy."""
    from . import workload

    try:
        cmd = workload.command(_sim(), node, gpus, duration, user, cpus)
    except workload.WorkloadError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    r = stack._run(cmd)
    out = (r.stdout or r.stderr or "").strip()
    typer.echo(f"{node}: job {out} submitted for {duration} s" if r.returncode == 0 else f"cannot submit on {node}: {out}",
               err=r.returncode != 0)
    raise typer.Exit(r.returncode)


load_app = typer.Typer(cls=SuggestingGroup, help="Simulated users submitting jobs")
app.add_typer(load_app, name="load")


@load_app.command("on")
def load_on(rate: float = typer.Option(6, "--rate", help="jobs per minute (Poisson arrivals)"),
            duration: float = typer.Option(120, "--duration", help="mean job length in seconds"),
            seed: int = typer.Option(None, "--seed", help="a fixed seed makes the arrivals repeatable")) -> None:
    """Start the user simulator: alice and bob submit cpu jobs and GPU jobs at random, so squeue and the node states have a live queue."""
    from . import workload

    try:
        cmds = workload.load_on(_sim(), rate, duration, seed)
    except workload.WorkloadError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    for cmd in cmds:
        r = stack._run(cmd)
        if r.returncode and cmd != workload.load_off():  # pkill finding nothing to stop is fine
            typer.echo(f"cannot start the load: {(r.stderr or r.stdout).strip()}", err=True)
            raise typer.Exit(1)
    typer.echo(f"load: on, about {rate:g} job(s) a minute, each about {duration:g} s (benchctl load off to stop)")


@load_app.command("off")
def load_off() -> None:
    """Stop the user simulator (jobs already queued keep running)."""
    from . import workload

    r = stack._run(workload.load_off())
    typer.echo("load: off" if r.returncode in (0, 1) else f"cannot stop the load: {(r.stderr or r.stdout).strip()}",
               err=r.returncode not in (0, 1))
    raise typer.Exit(0 if r.returncode in (0, 1) else 1)


@set_app.command("jira-activity")
def set_jira_activity(state: str = typer.Argument(..., help="on or off"),
                      every: float = typer.Option(None, "--every", help="seconds between changes (default 45)"),
                      seed: int = typer.Option(None, "--seed", help="a fixed seed makes the changes repeatable")) -> None:
    """Background changes in the Jira mock: a team moves tickets along, comments, reassigns, reopens and files new ones."""
    if state not in ("on", "off"):
        typer.echo("state must be on or off", err=True)
        raise typer.Exit(1)
    body = {"on": state == "on", **({"every": every} if every else {"every": 45}), **({"seed": seed} if seed is not None else {})}
    try:
        scenario.post(scenario.ENDPOINTS["jira-activity"], body)
    except (OSError, ValueError) as e:
        typer.echo(f"cannot reach the Jira mock: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"jira activity: {state}" + (f", a change about every {body['every']:g} s" if state == "on" else ""))


@app.command("exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def exec_cmd(ctx: typer.Context, node: str = TARGET,
             user: str = typer.Option(None, "-u", "--user", autocompletion=completion.users, help="run as this simulated user, e.g. alice")) -> None:
    """Run a command on a node, or open a shell: `benchctl exec gpu001 -- nvidia-smi`, `benchctl exec cn001`."""
    import os
    import sys

    cmd = ctx.args or ["bash"]
    tty = ["-it"] if sys.stdin.isatty() and not ctx.args else []
    os.execvp("docker", ["docker", "exec", *tty, *(["-u", user] if user else []), f"{stack.PROJECT}-{node}-1", *cmd])


@app.command()
def logs(node: str = TARGET, follow: bool = typer.Option(False, "-f", "--follow"),
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
          profile: str = typer.Option(None, "--profile", autocompletion=completion.profiles, callback=_profile_check, help="default: the profile `up` last started"),
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
def scenario_run(name: str = typer.Argument(..., autocompletion=completion.scenarios, help="see `scenario list`"),
                 speed: float = typer.Option(1.0, help="fast-forward: 10 plays it ten times faster"),
                 dry_run: bool = typer.Option(False, "--dry-run", help="validate and show the timeline only")) -> None:
    """Play a scenario against the running stack; exit 1 if a step or expectation fails."""
    sim = _sim()
    try:
        sc = scenario.load(name)
    except scenario.ScenarioError as e:
        typer.echo(completion.explain(name, completion.scenarios(""), "scenario") if completion.scenarios("") else str(e), err=True)
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
