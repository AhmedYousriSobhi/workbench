import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path as _Path

import typer
import typer.completion

from . import (
    agentmode,
    agentverbs,
    alerts,
    capture,
    checks,
    commands,
    completion,
    compose,
    contract,
    describe,
    diag,
    errors,
    export,
    faultstate,
    inventory,
    jira_real,
    lab,
    lab_apply,
    lab_candidate,
    lab_diff,
    lab_export,
    lab_validate,
    logins,
    machine,
    memory,
    netemfaults,
    nhc,
    nodes,
    ready,
    scenario,
    situation,
    slurmstats,
    slurmtrace,
    slurmver,
    stack,
    storage,
    teleport,
    testreport,
    tuning,
    wait,
    workbench,
)
from .commands.base import (
    JSON,
    OUTPUT,
    SuggestingGroup,
    _base_sim,
    _node_check,
    _profile_check,
    _sim,
    _target_check,
)
from .commands.shellcompletion import _completion_install

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


def _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, k8s_cpu_nodes, bmc_nodes=None, login=None, bmc_groups=None, bcm_k8s=None, controller=None, dcgm_nodes=None, slurm_version=None):
    """(spec, sim, profile) from a workbench file and/or flags; flags win. Exits 1 with every problem listed."""
    base = _base_sim()
    try:
        spec = workbench.load_file(file, workbench.defaults(base)) if file else workbench.defaults(base)
    except workbench.SpecError as e:
        typer.echo(f"invalid workbench: {e}", err=True)
        raise typer.Exit(1) from e
    for key, val in (("name", name), ("cpu_nodes", cpu_nodes), ("gpu_nodes", gpu_nodes),
                     ("gpus_per_node", gpus_per_node), ("k8s_nodes", k8s_nodes), ("k8s_cpu_nodes", k8s_cpu_nodes)):
        if val is not None:
            setattr(spec, key, val)
    spec.parts = sorted((set(spec.parts) | set(with_ or [])) - set(without or []))
    if bmc_nodes is not None:
        spec.bmc_nodes = [n for n in bmc_nodes.split(",") if n]
    if bmc_groups is not None:
        spec.bmc_groups = [g for g in bmc_groups.split(",") if g]
    if bcm_k8s is not None:
        spec.bcm_k8s = bcm_k8s
    if controller is not None:
        spec.controller = controller
    if dcgm_nodes is not None:
        spec.dcgm_nodes = dcgm_nodes
    if slurm_version is not None:
        spec.slurm_version = slurm_version
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
K8S = typer.Option(None, "--k8s-nodes", help="Kubernetes GPU nodes (needs --with k8s; default: sim.yaml)")
K8S_CPU = typer.Option(None, "--k8s-cpu-nodes", help="Kubernetes CPU nodes (needs --with k8s; default: sim.yaml)")
BMC_GROUPS = typer.Option(None, "--bmc-groups", help=f"node groups that get BMCs, comma separated ({', '.join(nodes.GROUPS)}); default all; each BMC costs about 32 MB")
BCM_K8S = typer.Option(None, "--bcm-k8s/--no-bcm-k8s", help="whether BCM manages the Kubernetes nodes (default: sim.yaml bcm.manage_kubernetes)")
CONTROLLER = typer.Option(None, "--controller/--no-controller", help="a controller node: slurmctld's host with ssh for whitelisted users, the BCM head node (default: sim.yaml controller.enabled)")
SLURM_VERSION = typer.Option(None, "--slurm-version", help="build and run this Slurm release (25.05.9, 25.11.9, 26.05.5 default, 26.05.6); the first start of another version compiles it (ADR 0079)")
DCGM_NODES = typer.Option(None, "--dcgm-nodes", help="GPU nodes that get a real DCGM exporter, 1 to 8 (needs --with dcgm; default 1); each costs about 125 MB")
BMC_NODES = typer.Option(None, "--bmc-nodes", help="nodes that get a BMC address, comma separated (default all); each costs about 32 MB")

def _login_modes(login) -> list[str]:
    try:
        return logins.parse(login)
    except logins.LoginError as e:
        typer.echo(f"invalid: {e}", err=True)
        raise typer.Exit(1) from e


LOGIN = typer.Option(None, "--login", autocompletion=completion.login_modes, rich_help_panel="Parts by category",
                     help=f"login nodes, one auth mode each: {', '.join(logins.MODES)} (comma separated), e.g. ssh,teleport; "
                          f"add :slurm or :k8s for an s-login or k-login, e.g. ssh:slurm,ssh:k8s (default both); "
                          "the first is `login`, then login2, ...")


def _category_option(cat: str):
    what = workbench.CATEGORIES[cat][0]
    return typer.Option(None, f"--{cat}", autocompletion=completion.category(cat), rich_help_panel="Parts by category",
                        help=f"{what}: {', '.join(workbench.items(cat))} (comma separated or repeated)")


WORKLOAD, ACCOUNTING, MONITORING, TICKETING, HARDWARE, ACCESS = (
    _category_option(c) for c in ("workload", "accounting", "monitoring", "ticketing", "hardware", "access"))
def _app_mb_set(value: int | None) -> int | None:
    """--app-mb N: the memory reserved for the app under test in the plan (0 leaves it out; it is already running)."""
    if value is not None:
        os.environ[memory.APP_ENV] = str(value)
    return value


APP_MB_OPT = typer.Option(None, "--app-mb", min=0, callback=_app_mb_set,
                          help=f"MB the plan reserves for the app under test (default ${memory.APP_ENV}, else the saved `measure app --save`, else {memory.APP_MB}); 0 when it already runs")


ADDONS = typer.Option(False, "--addons", rich_help_panel="Parts by category",
                      help="turn on every optional part (all categories); --without X still removes one")


def _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, addons, login=None) -> list[str] | None:
    """`--with` parts plus the ones the category flags and --addons name, or exit 1 with the one problem. None when
    nothing was chosen, so `up` can still tell a plain preset from a selection."""
    picked = {"workload": workload, "accounting": accounting, "monitoring": monitoring, "ticketing": ticketing,
              "hardware": hardware, "access": access}
    try:
        extra = workbench.expand(picked, addons)
    except workbench.SpecError as e:
        typer.echo(f"invalid: {e}", err=True)
        raise typer.Exit(1) from e
    need = set()
    if login is not None:
        entries = _login_modes(login)
        need |= {"teleport"} if any(logins.auth(m) in logins.VIA_TELEPORT for m in entries) else set()  # a login node that uses Teleport needs it on
        need |= {"k8s"} if any(logins.kind(m) == "k8s" for m in entries) else set()  # a k-login needs the Kubernetes part
        extra = sorted({*extra, *need})
    chosen = with_ is not None or addons or any(v is not None for v in picked.values()) or bool(need)
    return sorted(set(with_ or []) | set(extra)) if chosen else None


SAVE = typer.Option(None, "--save", help="write the selection to this workbench file")


@app.command()
def plan(file: str = FILE, name: str = NAME, cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS,
         with_: list[str] = WITH, without: list[str] = WITHOUT, k8s_nodes: int = K8S, k8s_cpu_nodes: int = K8S_CPU, save: str = SAVE,
         bmc_nodes: str = BMC_NODES, bmc_groups: str = BMC_GROUPS, bcm_k8s: bool = BCM_K8S, controller: bool = CONTROLLER, dcgm_nodes: int = DCGM_NODES, slurm_version: str = SLURM_VERSION, workload: list[str] = WORKLOAD, accounting: list[str] = ACCOUNTING,
         monitoring: list[str] = MONITORING, ticketing: list[str] = TICKETING, hardware: list[str] = HARDWARE,
         access: list[str] = ACCESS, addons: bool = ADDONS, login: list[str] = LOGIN, app_mb: int = APP_MB_OPT,
         output: str = OUTPUT, json_: bool = JSON) -> None:
    """Estimate the memory a workbench needs against what the host has free; exit 1 if it would not fit."""
    from pathlib import Path

    import yaml

    with_ = _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, addons, login)
    spec, sim, prof = _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, k8s_cpu_nodes, bmc_nodes, login, bmc_groups, bcm_k8s, controller, dcgm_nodes, slurm_version)
    est = workbench.estimate(sim, prof, running=workbench.running_services())
    free_mb = checks.mem_available() // (1024 * 1024)
    ok, msg = workbench.verdict(est, free_mb, spec=spec)
    as_json = contract.wanted(output, json_) == "json"
    if as_json:
        typer.echo(contract.dumps({"fits": ok, "verdict": msg, "free_mb": free_mb, "workbench": spec.to_dict(), "memory": est}))
    else:
        typer.echo(workbench.render(spec, est) + f"\n\n{msg}")
    if save:
        Path(save).write_text(yaml.safe_dump(spec.to_dict(), sort_keys=False))
        typer.echo(f"saved {save}; build it with: benchctl apply -f {save}", err=as_json)
    raise typer.Exit(0 if ok else 1)


def _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, k8s_cpu_nodes, bmc_nodes=None, login=None, bmc_groups=None, bcm_k8s=None, controller=None, dcgm_nodes=None, slurm_version=None) -> None:
    spec, sim, prof = _selection(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, k8s_cpu_nodes, bmc_nodes, login, bmc_groups, bcm_k8s, controller, dcgm_nodes, slurm_version)
    typer.echo(workbench.render(spec, workbench.estimate(sim, prof)) + "\n")
    if not stack.up(sim, "workbench", say=typer.echo, prof=prof):
        raise typer.Exit(1)
    workbench.save(spec)


@app.command()
def up(profile: str = typer.Option(None, "--profile", autocompletion=completion.profiles, callback=_profile_check, help="a size preset (default std); or choose by counts below"),
       file: str = FILE, name: str = NAME, cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS,
       with_: list[str] = WITH, without: list[str] = WITHOUT, k8s_nodes: int = K8S, k8s_cpu_nodes: int = K8S_CPU, bmc_nodes: str = BMC_NODES, bmc_groups: str = BMC_GROUPS, bcm_k8s: bool = BCM_K8S, controller: bool = CONTROLLER, dcgm_nodes: int = DCGM_NODES, slurm_version: str = SLURM_VERSION,
       workload: list[str] = WORKLOAD, accounting: list[str] = ACCOUNTING, monitoring: list[str] = MONITORING,
       ticketing: list[str] = TICKETING, hardware: list[str] = HARDWARE, access: list[str] = ACCESS,
       addons: bool = ADDONS, login: list[str] = LOGIN, app_mb: int = APP_MB_OPT) -> None:
    """Estimate, then start. By preset (--profile) or by selection (--cpu-nodes 2 --gpu-nodes 4 --with jira,
    --ticketing jira --hardware bmc,bcm, --addons ...)."""
    with_ = _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, addons, login)
    chosen = any(v is not None for v in (file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, k8s_cpu_nodes, bmc_nodes, login, bmc_groups, bcm_k8s, controller, dcgm_nodes, slurm_version))
    if chosen and profile:
        typer.echo("choose one: --profile PRESET, or a selection (--cpu-nodes, --gpu-nodes, --with, -f FILE)", err=True)
        raise typer.Exit(1)
    if chosen:
        _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, k8s_cpu_nodes, bmc_nodes, login, bmc_groups, bcm_k8s, controller, dcgm_nodes, slurm_version)
        return
    workbench.forget()
    raise typer.Exit(0 if stack.up(_base_sim(), profile or "std", say=typer.echo) else 1)


@app.command()
def apply(resource: str = typer.Argument(None, help="`lab NAME` puts a config lab on the running cluster through the governor (see `benchctl diff lab`)"),
          lab_name: str = typer.Argument(None, help="the lab to apply (with `lab`)"),
          file: str = typer.Option(None, "-f", "--file", help="workbench file to build"), name: str = NAME,
          cpu_nodes: int = CPU, gpu_nodes: int = GPU, gpus_per_node: int = GPUS, with_: list[str] = WITH,
          without: list[str] = WITHOUT, k8s_nodes: int = K8S, k8s_cpu_nodes: int = K8S_CPU, bmc_nodes: str = BMC_NODES, bmc_groups: str = BMC_GROUPS, bcm_k8s: bool = BCM_K8S, controller: bool = CONTROLLER, dcgm_nodes: int = DCGM_NODES, slurm_version: str = SLURM_VERSION,
          workload: list[str] = WORKLOAD, accounting: list[str] = ACCOUNTING, monitoring: list[str] = MONITORING,
          ticketing: list[str] = TICKETING, hardware: list[str] = HARDWARE, access: list[str] = ACCESS,
          addons: bool = ADDONS, login: list[str] = LOGIN, app_mb: int = APP_MB_OPT,
          side: str = typer.Option("baseline", "--side", help="with `lab`: baseline (as imported) or candidate (your edits)"),
          execute: bool = typer.Option(False, "--execute", hidden=True, help="with `lab`: run here, not through the governor (the governor's own child)")) -> None:
    """Build the workbench a file describes (the same as `up -f FILE`), or `apply lab NAME [--side baseline|candidate]`: put one side of a config lab
    on the running cluster through the governor (L0-L1): QOS and accounts, Kubernetes, Prometheus rules, Teleport roles, slurm.conf keys, then
    reconfigure or restart; verifies `scontrol show config`. `benchctl remove lab NAME` undoes it."""
    if resource is not None:
        if resource != "lab" or not lab_name:
            typer.echo("usage: benchctl apply lab NAME [--side baseline|candidate]   (or: benchctl apply -f FILE)", err=True)
            raise typer.Exit(2)
        _lab_run("apply", lab_name, side, execute)
    if file is None:
        typer.echo("Missing option '-f' / '--file' (or use: benchctl apply lab NAME)", err=True)
        raise typer.Exit(2)
    with_ = _with_parts(with_, workload, accounting, monitoring, ticketing, hardware, access, addons, login)
    _build(file, name, cpu_nodes, gpu_nodes, gpus_per_node, with_, without, k8s_nodes, k8s_cpu_nodes, bmc_nodes, login, bmc_groups, bcm_k8s, controller, dcgm_nodes, slurm_version)


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


app.add_typer(commands.shellcompletion.completion_app, name="completion")


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


YES = typer.Option(False, "-y", "--yes", help="do not ask (a terminal asks; scripts are never asked; BENCHCTL_AGENT=1 requires it)")


FORCE_RUN = typer.Option(False, "--force", help="while a config lab run is active: abort it, skip its restore and keep the snapshot "
                         "(`benchctl remove lab NAME` then returns to the plain cluster); BENCHCTL_AGENT=1 also needs --yes")
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


def _confirm(what: str, yes: bool, ask: bool = True) -> None:
    """Ask before a step that ends shells or removes nodes, but only for a person at a terminal: scripts and the
    launcher (no TTY) are never blocked, and `--yes` skips the question. With BENCHCTL_AGENT=1 nobody can be asked, so
    the step is refused without `--yes` (ADR 0067); `ask=False` adds only that rule, for verbs that never asked a person."""
    if not yes and agentmode.agent():
        errors.needs_yes(what)
        raise typer.Exit(1)
    if ask and not yes and sys.stdin.isatty() and not typer.confirm(f"{what} Continue?", default=False):
        typer.echo("cancelled", err=True)
        raise typer.Exit(1)


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


NODE = typer.Argument(..., autocompletion=completion.nodes, callback=_node_check, help="node name, e.g. cn001 or gpu001")
TARGET = typer.Argument(..., autocompletion=completion.targets, callback=_target_check,
                        help="node or service, e.g. cn001, gpu001, slurmctld")
SELECTOR = typer.Option(None, "-l", "--selector", autocompletion=completion.selectors, help="keep rows where key=value or key!=value (repeat or comma-separate)")
SORT_BY = typer.Option(None, "--sort-by", autocompletion=completion.columns, help="sort by a column name")
WATCH = typer.Option(False, "-w", "--watch", help="redraw until Ctrl-C")
INTERVAL = typer.Option(2.0, "--interval", help="seconds between redraws with --watch (at least 1)")

get_app = typer.Typer(cls=SuggestingGroup, help="List what exists: nodes, services, profiles, scenarios")
app.add_typer(get_app, name="get")


@get_app.command("ticket-bot")
def get_ticket_bot() -> None:
    """The settings the running ticket bot was last given with `benchctl set ticket-bot` (empty: its start values, see `load on --tickets`)."""
    from . import workload
    r = stack._run(workload.ticket_settings_get())
    if r.returncode:
        typer.echo("cannot reach the login node: start the cluster first (benchctl up)", err=True)
        raise typer.Exit(1)
    typer.echo(r.stdout.strip())


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
            typer.echo(("\033[2J\033[H" if sys.stdout.isatty() and not agentmode.agent() else "") + text)
            time.sleep(interval)
    except inventory.QueryError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@get_app.command("nodes")
def get_nodes(output: str = OUTPUT, json_: bool = JSON, selector: list[str] = SELECTOR, sort_by: str = SORT_BY, watch: bool = WATCH,
              interval: float = INTERVAL) -> None:
    """Every node sim.yaml defines, its kind and container state (absent = not started). Example: -l kind=gpu."""
    _get(lambda: inventory.node_rows(_sim()), contract.wanted(output, json_), selector, sort_by, watch, interval)


@get_app.command("services")
def get_services(output: str = OUTPUT, json_: bool = JSON, selector: list[str] = SELECTOR, sort_by: str = SORT_BY, watch: bool = WATCH,
                 interval: float = INTERVAL) -> None:
    """Every container of the running stack with its state."""
    _get(inventory.service_rows, contract.wanted(output, json_), selector, sort_by, watch, interval)


@get_app.command("workbench")
def get_workbench(output: str = OUTPUT, json_: bool = JSON) -> None:
    """The workbench last built by `up`/`apply` with a selection (none after a plain `up --profile`)."""

    import yaml

    output = contract.wanted(output, json_)
    spec = workbench.current(_base_sim())
    if spec is None:
        typer.echo(contract.dumps({"workbench": None}) if output == "json" else "none: the last start used a size preset (benchctl get profiles)")
        return
    data = spec.to_dict()
    typer.echo(contract.dumps({"workbench": data}) if output == "json" else yaml.safe_dump(data, sort_keys=False).rstrip())


@get_app.command("profiles")
def get_profiles(output: str = OUTPUT, json_: bool = JSON, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """The size presets for `benchctl up --profile`."""
    _get(inventory.profile_rows, contract.wanted(output, json_), selector, sort_by, False, 2.0)


@get_app.command("checks")
def get_checks(output: str = OUTPUT, json_: bool = JSON, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """The node health checks (NHC) in the order they run, whether each is on, its parameters. Templates: `-o name` lists the checks; see `add check --help`."""
    try:
        _get(inventory.check_rows, contract.wanted(output, json_), selector, sort_by, False, 2.0)
    except nhc.NhcError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


TRACE_NODES = typer.Option([], "--node", "-n", help="a node to watch (repeat), e.g. cn001")
TRACE_SINCE = typer.Option(60, "--since", help="log lines from this many seconds ago")


@get_app.command("trace")
def get_trace(node: list[str] = TRACE_NODES, since: float = TRACE_SINCE) -> None:
    """What Slurm shows now (jobs, slurmd and slurmstepd pids per node) and what slurmctld and the nodes logged lately, as JSON:
    the data behind `scenario run --trace` and the page's Slurm trace. Read-only."""
    typer.echo(json.dumps(slurmtrace.collect(node, time.time() - since, lambda c: stack._run(c))))


@get_app.command("slurm-stats")
def get_slurm_stats() -> None:
    """slurmctld's request counts and times from `sdiag`, as JSON: the data behind the page's Traffic view. Read-only."""
    typer.echo(json.dumps(slurmstats.collect(lambda c: stack._run(c))))


@get_app.command("slurm-checksum")
def get_slurm_checksum(version: str = typer.Argument(..., help="a Slurm release, e.g. 25.11.9")) -> None:
    """Download SchedMD's tarball for a release (at most 100 MB and 5 minutes, nothing kept), hash it and compare with the SHA256 digest
    GitHub publishes for the same release asset (a second, independent source). Both are printed; a disagreement is refused (exit 1,
    WB-REFUSED-CHECKSUM). Without a GitHub digest the computed hash is advice only. Prints the line to paste into benchctl/slurmver.py;
    it never edits the file. For a recorded release it also says whether the hash matches. Needs internet."""
    try:
        sha = slurmver.fetch_sha256(version)
        digest, why = slurmver.github_digest(version)
        both = slurmver.compare(version, sha, digest, why)
        if version in slurmver.VERSIONS:
            typer.echo(both)
            typer.echo(slurmver.check(version, sha))
            return
    except slurmver.DigestDisagreement as e:
        typer.echo(errors.render("WB-REFUSED-CHECKSUM", str(e)), err=True)
        raise typer.Exit(1) from e
    except slurmver.SlurmVersionError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"slurm-{version}.tar.bz2\n{both}\nTo accept it, add this line to VERSIONS in benchctl/slurmver.py:\n{slurmver.table_line(version, sha)}")


CAPTURE_NODE = typer.Argument(..., help="a Slurm node or a Kubernetes node, e.g. cn001, gpu001 or kw001")
CAPTURE_SECONDS = typer.Option(None, "--seconds", min=1, max=300, help="stop after this many seconds (default 300 streaming, 10 with --once; at most 300, 60 with --once)")
CAPTURE_ONCE = typer.Option(False, "--once", help="one bounded answer: a single JSON object with the messages seen (for scripts and agents); asks the governor first")
CAPTURE_MESSAGES = typer.Option(agentverbs.CAPTURE_ONCE_MESSAGES, "--max-messages", min=1, max=agentverbs.CAPTURE_ONCE_MAX_MESSAGES, help="with --once: stop after this many messages")


@get_app.command("capture")
def get_capture(node: str = CAPTURE_NODE, seconds: int = CAPTURE_SECONDS, once: bool = CAPTURE_ONCE, max_messages: int = CAPTURE_MESSAGES,
                output: str = OUTPUT, json_: bool = JSON) -> None:
    """The messages on one node's wire as they happen, one JSON object per line (issue 505): Slurm messages by tcpdump inside the node,
    or the Kubernetes API audit events about the node. Nothing from a message body; nothing written to disk. The page asks the governor
    for it; it ends by itself after --seconds. `--once` waits for the limit (10 s, 200 messages by default) and prints one JSON object
    with every message instead (issue 635; `--json` means the same as `--once`); like the page it is refused when the governor admits no new work (memory level L2 and above)."""
    import signal
    import sys

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # the governor stops it with SIGTERM: let tcpdump be stopped on the way out
    once = once or contract.wanted(output, json_) == "json"
    try:
        if once:
            if seconds is not None and seconds > agentverbs.CAPTURE_ONCE_MAX_SECONDS:
                raise typer.BadParameter(f"with --once the longest wait is {agentverbs.CAPTURE_ONCE_MAX_SECONDS} seconds", param_hint="--seconds")
            if not stack.governor_admits(0, lambda m: typer.echo(m, err=True)):
                typer.echo(errors.render("WB-REFUSED-GOVERNOR", "no capture: the governor admits no new work at this memory level"), err=True)
                raise typer.Exit(1)
            typer.echo(contract.dumps(agentverbs.capture_once(_sim(), node, seconds or agentverbs.CAPTURE_ONCE_SECONDS, max_messages)))
            return
        capture.run(_sim(), node, lambda m: typer.echo(json.dumps(m), nl=True) or sys.stdout.flush(), seconds or capture.DEFAULT_SECONDS)
    except (ValueError, RuntimeError) as e:
        typer.echo(f"capture failed: {e}", err=True)
        raise typer.Exit(1) from e


def _endpoint_rows(full: bool) -> list[dict]:
    """The Connections registry (the page's Connections view): where to reach each entity and where its credential is (a path, never its value)."""
    from webui import connections as conn

    keep = ("id", "name", "kind", "address", "credential") + (("command",) if full else ())
    return [{k: e.get(k) if k != "credential" else (e.get(k) or "-") for k in keep} for e in conn.entities(_sim())]


@get_app.command("lab")
def get_lab(name: str = typer.Argument(..., help="a lab made with `benchctl import lab`"),
            max_nodes: int = typer.Option(None, "--max-nodes", min=1, max=64, help="lab node budget (default lab.max_nodes, 24)"),
            keep_users: bool = typer.Option(False, "--keep-users", help="keep production user names instead of lab-u001..."),
            target_hours: float = typer.Option(None, "--target-hours", help="run length per side, 0.5 to 6 (default lab.target_hours, 2)"),
            output: str = OUTPUT) -> None:
    """Show how a lab would be downscaled: nodes per group, sampled jobs, user mapping, time dilation, key classes and memory. Changes nothing."""
    try:
        p = lab.plan(name, _base_sim(), max_nodes=max_nodes, keep_users=keep_users, target_hours=target_hours)
    except lab.LabError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(json.dumps(p, indent=2) if output == "json" else lab.plan_text(p))


@get_app.command("endpoints")
def get_endpoints(output: str = OUTPUT, json_: bool = JSON, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """Every address a client can reach (SSH logins, BMC, BCM, Jira, Kubernetes...), the command that works and the file that holds its credential. `--json` adds the command."""
    as_json = contract.wanted(output, json_)
    _get(lambda: _endpoint_rows(as_json == "json"), as_json, selector, sort_by, False, 2.0)


FAULT_STATES = ("down", "fault", "warn")  # the page's node states other than healthy (ok) and no data (unknown), webui/cluster.py classify


def _fault_rows() -> list[dict]:
    from webui import cluster

    sim = _sim()
    snap = cluster.Cluster(sim).snapshot()
    rows = [{"target": n["name"], "kind": n["kind"], "state": n["state"], "label": n["label"]} for n in snap["nodes"] if n.get("state") in FAULT_STATES]
    running = [r["name"] for r in inventory.node_rows(sim) if r["state"] == "running" and r["kind"] != "k8s"]
    return rows + faultstate.collect(cluster.http_json, stack._run, running)


@get_app.command("faults")
def get_faults(output: str = OUTPUT, json_: bool = JSON, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """What is wrong right now: unhealthy nodes (powered off, a GPU error, overheating, a failed fan, an InfiniBand or NIC problem: the page's node cards) and the faults on a service or a credential (a Jira failure mode, an expired BMC or Kubernetes certificate, an expired Slurm token, a wrong munge key). None when all is well."""
    _get(_fault_rows, contract.wanted(output, json_), selector, sort_by, False, 2.0)


@get_app.command("traffic")
def get_traffic(output: str = OUTPUT, json_: bool = JSON,
                since: float = typer.Option(0.0, "--since", min=0.0, help="only requests newer than this time (seconds since the epoch); streams are always cumulative")) -> None:
    """Who called which mock, how it ended and how long it took: the mocks' own record of client requests (the page's Traffic analysis). Read-only."""
    from webui import traffic

    data = traffic.collect(since)
    if contract.wanted(output, json_) == "json":
        typer.echo(contract.dumps(data))
        return
    down = [s for s, v in data["services"].items() if v is False]
    rows = [{"service": s["service"], "who": s["who"], "shape": s["shape"], "n": s["n"], "ok": s["ok"],
             "client_error": s["client_error"], "server_error": s["server_error"], "ms": s["ms"]} for s in data["streams"]]
    typer.echo(inventory.render(rows) if rows else "no requests recorded")
    if down:
        typer.echo(f"not reachable: {', '.join(down)}", err=True)

@get_app.command("alerts")
def get_alerts(output: str = OUTPUT, json_: bool = JSON, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """The alerts Prometheus is firing or about to fire (read-only). The workbench ships seven default rules (GpuXidCritical, GpuTempHigh, InfinibandPortDown, NodeDrained, NodeDown, ExporterDown, JiraErrors), so empty means none is firing. Exit 1 when Prometheus is not running."""
    def rows() -> list[dict]:
        try:
            return alerts.fetch()
        except alerts.NotRunning as e:
            typer.echo(errors.render("WB-PART-NOT-RUNNING", f"{e}; start it with: {alerts.ENABLE}"), err=True)
            raise typer.Exit(1) from e
    _get(rows, contract.wanted(output, json_), selector, sort_by, False, 2.0)


@get_app.command("scenario-status")
def get_scenario_status(output: str = OUTPUT, json_: bool = JSON) -> None:
    """The web page's scenario run: running or not, which one, each step's status and the `expect` verdicts so far (read-only, issue 635).
    A `scenario run` shows here too while the page answers (ADR 0086 phase 4); that command blocks and prints its own report. Exit 1 when the page is not running."""
    try:
        data = agentverbs.scenario_status(_page_run_state())
    except (OSError, ValueError) as e:
        typer.echo(errors.render("WB-PART-NOT-RUNNING", f"the web page does not answer ({e}); start it with: benchctl start"), err=True)
        raise typer.Exit(1) from e
    if contract.wanted(output, json_) == "json":
        typer.echo(contract.dumps(data))
        return
    typer.echo(f"{data['name'] or 'no scenario run yet'}: {'running' if data['running'] else 'not running'}"
               + (f", {data['steps_done']}/{data['steps_total']} steps" if data["steps_total"] else "") + (f", ok={data['ok']}" if data["ok"] is not None else ""))


@get_app.command("scenarios")
def get_scenarios(output: str = OUTPUT, json_: bool = JSON, selector: list[str] = SELECTOR, sort_by: str = SORT_BY) -> None:
    """The fault and workload scenarios for `benchctl scenario run`."""
    _get(inventory.scenario_rows, contract.wanted(output, json_), selector, sort_by, False, 2.0)


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


@set_app.command("netem")
def set_netem(node: str = typer.Argument(..., help="a Slurm node of this cluster, e.g. cn001"),
              delay: str = typer.Option(None, "--delay", help="added latency, 1ms to 500ms, e.g. 200ms"),
              loss: str = typer.Option(None, "--loss", help="packet loss, 0.1% to 20%, e.g. 5%"),
              ttl: str = typer.Option("60s", "--ttl", help="the fault ends by itself after this long, 1s to 10m (60s by default)")) -> None:
    """`benchctl set netem cn001 --delay 200ms --loss 5% --ttl 60s` slows the node's own network with `tc netem` (issue 478, ADR 0056).
    Only the optional root governor service can do it (`sudo ./scripts/install-governor.sh`); the page never offers it. Bounded targets
    (Slurm nodes of this cluster), bounded values, always a TTL, only at memory levels L0-L1. `benchctl clear netem` removes it early;
    the governor also clears every rule when it starts or stops."""
    do = {"nodes": [node], "ttl": ttl, **({"delay": delay} if delay else {}), **({"loss": loss} if loss else {})}
    if bad := netemfaults.problems(do, {n for n, _ in nodes.node_names(_sim())}):
        typer.echo("\n".join(bad), err=True)
        raise typer.Exit(1)
    if why := netemfaults.apply(do):
        typer.echo(f"cannot set netem on {node}: {why}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{node}: netem {' '.join(f'{k} {v}' for k, v in (('delay', delay), ('loss', loss)) if v)}, ends in {ttl} (`benchctl clear netem` removes it now)")


@set_app.command("ticket-bot")
def set_ticket_bot(every: float = typer.Option(None, "--every", help="mean seconds between actions (10 to 3600)"),
                   resolve: float = typer.Option(None, "--resolve", help="seconds a ticket stays In Progress before it is resolved (10 to 86400)"),
                   ignore: float = typer.Option(None, "--ignore", help="share of tickets nobody picks up (0 to 1)"),
                   reopen: float = typer.Option(None, "--reopen", help="share of resolved tickets that are reopened (0 to 1)")) -> None:
    """Change the running ticket bot without a restart (`benchctl load on --tickets` first): it re-reads the settings each cycle.
    `benchctl set ticket-bot --every 10 --resolve 30` makes a test fast; `benchctl get ticket-bot` shows what is in force."""
    from . import workload
    try:
        cmd = workload.ticket_settings_set(every=every, resolve_after=resolve, ignore=ignore, reopen=reopen)
    except workload.WorkloadError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    r = stack._run(cmd)
    if r.returncode:
        typer.echo("cannot reach the login node: start the cluster first (benchctl up)", err=True)
        raise typer.Exit(1)
    typer.echo(f"ticket bot settings: {r.stdout.strip()}")


clear_app = typer.Typer(cls=SuggestingGroup, help="Put things back: the injected node faults")
app.add_typer(clear_app, name="clear")


@clear_app.command("faults")
def clear_faults_cmd(output: str = OUTPUT, json_: bool = JSON) -> None:
    """Clear every injected node fault at once, as the page's Clear faults does for each node (a hot or failed-fan BMC, a GPU XID, an
    InfiniBand or NIC problem; a powered-off node is powered on). Faults on a service or a credential are not touched (scenarios restore
    those). Safe to repeat. `benchctl get faults` shows what is left."""
    run, post = (lambda c: stack._run(c)), (lambda url, body: scenario.post(url, body))
    data = agentverbs.clear_faults(_sim(), post, run)
    if contract.wanted(output, json_) == "json":
        typer.echo(contract.dumps(data))
    else:
        for r in data["cleared"]:
            typer.echo(f"{r['target']}: {r['status']}" + (f" ({r['detail']})" if r["detail"] else ""))
    raise typer.Exit(0 if data["ok"] else 1)


@clear_app.command("netem")
def clear_netem_cmd(node: str = typer.Argument(None, help="one node; every node when left out")) -> None:
    """Remove the `tc netem` faults the root governor service set (all nodes, or one). Safe to repeat, allowed at every memory level."""
    do = {"clear": True, **({"nodes": [node]} if node else {})}
    if bad := netemfaults.problems(do, {n for n, _ in nodes.node_names(_sim())}):
        typer.echo("\n".join(bad), err=True)
        raise typer.Exit(1)
    if why := netemfaults.apply(do):
        typer.echo(f"cannot clear netem: {why}", err=True)
        raise typer.Exit(1)
    typer.echo(f"netem cleared on {node or 'every node'}")


CHECK = typer.Argument(..., autocompletion=completion.checks, help="check name, see `benchctl get checks`")
SETS = typer.Option(None, "--set", help="a parameter, key=value (repeat for several)")
POSITION = typer.Option(None, "--position", help="place in the run order, 1 = first")

add_app = typer.Typer(cls=SuggestingGroup, help="Add something: a health check, a Teleport invite")
app.add_typer(add_app, name="add")
remove_app = typer.Typer(cls=SuggestingGroup, help="Remove something: a health check, the simulated users' jobs")
app.add_typer(remove_app, name="remove")
validate_app = typer.Typer(cls=SuggestingGroup, help="Check files without changing anything: the health checks, a config lab")
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
        teleport.snapshot()  # the user's password is chosen in the browser; the next `down` saves that too
    except teleport.TeleportError as e:
        typer.echo(f"{e}\nIs Teleport running? Start it with `benchctl up --with teleport` (see `benchctl get services`).", err=True)
        raise typer.Exit(1) from e


def _daemon(do: dict, what: str) -> None:
    o = scenario.execute(scenario.Step(0, "slurm", do), host=True, runner=lambda c: stack._run(c))
    typer.echo(what if o.status == "ok" else f"cannot {what}: {o.detail}", err=o.status != "ok")
    raise typer.Exit(0 if o.status == "ok" else 1)


ACTIONS = {"restart": "restart", "stop": "stop", "start": "start"}


@set_app.command("slurmd")
def set_slurmd(node: str = TARGET, action: str = typer.Argument(..., help="restart (kill it; it starts again), stop (keep it down) or start")) -> None:
    """`benchctl set slurmd cn001 restart` kills slurmd on the node; running jobs keep their slurmstepd (see the Slurm resilience guide). `stop` keeps it down until `start`."""
    if action not in ACTIONS:
        typer.echo("action must be restart, stop or start", err=True)
        raise typer.Exit(1)
    _daemon({f"slurmd_{action}": [node]}, f"slurmd on {node}: {action}")


@set_app.command("slurmctld")
def set_slurmctld(action: str = typer.Argument(..., help="restart")) -> None:
    """`benchctl set slurmctld restart` restarts the controller's container; the nodes keep their jobs."""
    if action != "restart":
        typer.echo("action must be restart", err=True)
        raise typer.Exit(1)
    _daemon({"slurmctld_restart": True}, "slurmctld: restart")


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


app.add_typer(commands.jira_real.refresh_app, name="refresh")
app.add_typer(commands.jira_real.setup_app, name="setup")


@remove_app.command("check")
def remove_check(name: str = CHECK, yes: bool = YES) -> None:
    """Remove a check and its file from images/node-base/nhc.d (git keeps the old one). To keep it, `set check NAME off` instead."""
    _confirm(f"This removes the check {name} and its file.", yes, ask=False)
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


app.add_typer(commands.load.load_app, name="load")


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


run_app = typer.Typer(cls=SuggestingGroup, help="Run something: a config lab's replay, ticket automation against the mock or the real Jira")
app.add_typer(run_app, name="run")


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


@run_app.command("lab")
def run_lab(name: str = typer.Argument(..., help="a lab made with `benchctl import lab`"),
            ab: bool = typer.Option(False, "--ab", help="replay the baseline, restore the snapshot, replay the candidate (default: the candidate only)"),
            target_hours: float = typer.Option(None, "--target-hours", help="run length per side, 0.5 to 6 (default lab.target_hours, 2)"),
            seed: int = typer.Option(None, "--seed", min=0, help="sampling seed, recorded in the run (default 1, the one `get lab` uses)"),
            prepare: bool = typer.Option(False, "--prepare", hidden=True, help="freeze the jobs and print the run (the governor's child)"),
            run_id: str = typer.Option(None, "--run", hidden=True, help="with --execute: the prepared run"),
            execute: bool = typer.Option(False, "--execute", hidden=True, help="walk a prepared run here (the governor's child)"),
            recover: bool = typer.Option(False, "--recover", hidden=True, help="restore-only: finish a run whose child is gone (the governor's child)"),
            output: str = OUTPUT) -> None:
    """Replay a config lab's sampled trace on the running cluster through the governor (L0-L1, admitted): with --ab the baseline, then
    the candidate from the same snapshot; without it the candidate only. Returns once the run started; `benchctl status` shows it,
    `benchctl stop lab NAME` ends it. L2 pauses it, L3 aborts it."""
    from . import lab_run, lab_steps
    if target_hours is not None and not 0.5 <= target_hours <= 6:
        typer.echo(f"--target-hours must be from 0.5 to 6, not {target_hours:g}", err=True)
        raise typer.Exit(1)
    try:
        if prepare:
            p = lab_steps.prepare(name, _base_sim(), ab, target_hours, lab_run.DEFAULT_SEED if seed is None else seed)
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
    except lab.LabError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    req = {"cmd": "lab-run", "name": name, "ab": ab, **({"target_hours": target_hours} if target_hours is not None else {}),
           **({"seed": seed} if seed is not None else {})}
    if _lab_request(req, lambda r: f"lab {name}: preparing the run (freezing the jobs, then the memory check)").get("dry_run"):
        return
    raise typer.Exit(_lab_run_outcome(name))


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


commands.tickets.register(run_app)


@app.command("exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def exec_cmd(ctx: typer.Context, node: str = TARGET,
             user: str = typer.Option(None, "-u", "--user", autocompletion=completion.users, help="run as this simulated user, e.g. alice")) -> None:
    """Run a command on a node, or open a shell: `benchctl exec gpu001 -- nvidia-smi`, `benchctl exec cn001`."""
    import os
    import sys

    cmd = ctx.args or ["bash"]
    tty = ["-it"] if sys.stdin.isatty() and not ctx.args else []
    os.execvp("docker", ["docker", "exec", *tty, *(["-u", user] if user else []), f"{stack.PROJECT}-{node}-1", *cmd])


@app.command("ssh", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def ssh_cmd(ctx: typer.Context, target: str = typer.Argument(..., help="NODE for a root shell, USER@NODE for a login as that user, e.g. cn001 or alice@cn001")) -> None:
    """Open a shell on a node: `benchctl ssh cn001` (root), `benchctl ssh alice@cn001` (that user over SSH). A command after `--` runs instead."""
    import os
    import sys

    from . import nodeshell

    try:
        node = nodeshell.split(target)[1]
        known = completion.targets("")
        if not known:
            raise ValueError("the cluster is not running; start it with ./scripts/benchctl start")
        if node not in known:
            raise ValueError(f"unknown node {node!r}; try `benchctl get nodes`")
        cmd = nodeshell.argv(target, _sim(), str(stack.SSH_DIR / "id_ed25519"), ctx.args, sys.stdin.isatty())
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    os.execvp(cmd[0], cmd)


@app.command()
def logs(node: str = TARGET, follow: bool = typer.Option(False, "-f", "--follow"),
         tail: int = typer.Option(50, "--tail")) -> None:
    """Container log of a node or service. With BENCHCTL_AGENT=1 the log goes to a file and only its path is printed."""
    import os
    from pathlib import Path

    if agentmode.agent():
        if follow:
            raise errors.CodedBadParameter("--follow never ends: read a bounded log with --tail N", "WB-USAGE")
        r = stack._run(["docker", "logs", "--tail", str(tail), f"{stack.PROJECT}-{node}-1"])
        out = Path("compose/generated/logs")
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{node}.log"
        path.write_text(r.stdout + r.stderr)
        typer.echo(f"{path} ({len((r.stdout + r.stderr).splitlines())} lines, last {tail} of {node}); read it with: tail -n 40 {path}")
        raise typer.Exit(r.returncode)
    os.execvp("docker", ["docker", "logs", *(["-f"] if follow else []), "--tail", str(tail),
                         f"{stack.PROJECT}-{node}-1"])


app.add_typer(commands.serve.serve_app, name="serve")


describe_app = typer.Typer(cls=SuggestingGroup, help="Everything known about one thing")
app.add_typer(describe_app, name="describe")


@describe_app.command("node")
def describe_node(node: str = NODE, output: str = OUTPUT, json_: bool = JSON) -> None:
    """Container, Slurm state and jobs, GPUs (busy/idle), BMC power and temperature, how to ssh in."""

    try:
        info = describe.gather(_sim(), node, describe.http_get, stack._run)
    except describe.UnknownNode as e:
        typer.echo(str(e.args[0]), err=True)
        raise typer.Exit(1) from e
    typer.echo(contract.dumps(info) if contract.wanted(output, json_) == "json" else describe.render(info))


@describe_app.command("workbench")
def describe_workbench(output: str = OUTPUT, json_: bool = JSON) -> None:
    """One call for situational awareness: state, clusters and pools, endpoints (credentials as file paths only), faults, scenarios, fault targets and what to do next. `--json` is at most 6,500 characters."""
    import yaml

    from webui import cluster
    from webui import connections as conn

    sim = _sim()
    spec = workbench.current(_base_sim())
    gov = None
    try:
        gov = checks.governor_request({"cmd": "status"}, checks.GOV_SOCKET)
    except OSError:
        pass
    running = [r["name"] for r in inventory.service_rows() if r["state"] == "running"]
    state = {"containers": len(running), "profile": stack.current_profile() if running else None,
             "governor_level": gov["level"] if gov else None, "mem_available_mb": checks.mem_available() // (1024 * 1024)}
    info = situation.gather(
        state=state, spec=spec.to_dict() if spec else None, sim=sim, k8s_nodes=sim.get("kubernetes", {}).get("nodes", []) if cluster.k8s_built() else [],
        entities=conn.entities(sim), faults=_fault_rows(), scenarios=scenario.list_all(sim=sim), plan=scenario.plan,
        fault_targets=[*cluster.ACTIONS, *sorted(scenario.HOST_TARGETS), *sorted(k for k in scenario.ENDPOINTS if not k.startswith("jira-"))])
    if contract.wanted(output, json_) == "json":
        typer.echo(contract.dumps(info, compact=True))
        return
    typer.echo(yaml.safe_dump(info, sort_keys=False, width=120).rstrip())


wait_app = typer.Typer(cls=SuggestingGroup, help="Block until something is true, then print one JSON result (exit 0 met, 1 timeout, 2 precondition not met)")
app.add_typer(wait_app, name="wait")
TIMEOUT = typer.Option(120.0, "--timeout", min=1.0, help="seconds to wait before giving up (exit 1)")


def _wait(check, timeout: float, what: dict) -> None:
    result = wait.poll(check, timeout)
    if result["outcome"] == wait.PRECONDITION:
        typer.echo(errors.render(result["code"], result["message"], fix=result["fix"]), err=True)
    typer.echo(contract.dumps({"wait": what, **result}))
    raise typer.Exit(wait.EXIT[result["outcome"]])


@wait_app.command("ready")
def wait_ready(timeout: float = TIMEOUT) -> None:
    """Until every started service answers (the same check `start` uses). Exit 2 when nothing is running."""
    _wait(wait.ready_check(lambda: [r["name"] for r in inventory.service_rows() if r["state"] == "running"],
                           lambda n: ready.probe_ok(n, stack._run, stack.dc)), timeout, {"for": "ready"})


@wait_app.command("node-state")
def wait_node_state(node: str = NODE, state: str = typer.Argument(..., help="a container state as `get nodes` shows it: running, paused, absent..."),
                    timeout: float = TIMEOUT) -> None:
    """Until a node's state (as `get nodes` shows it) is STATE."""
    _wait(wait.node_state_check(lambda: inventory.node_rows(_sim()), node, state), timeout, {"for": "node-state", "node": node, "state": state})


@wait_app.command("alert")
def wait_alert(name: str = typer.Argument(..., help="the alert's name (alertname)"),
               resolved: bool = typer.Option(False, "--resolved", help="until it is no longer pending or firing (default: until it fires)"),
               firing: bool = typer.Option(False, "--firing", help="until it fires (the default)"),
               timeout: float = TIMEOUT) -> None:
    """Until a Prometheus alert fires (or, with --resolved, is gone). Exit 2 when Prometheus is not running."""
    if resolved and firing:
        raise typer.BadParameter("choose --firing or --resolved")
    want = "resolved" if resolved else "firing"
    _wait(wait.alert_check(name, want), timeout, {"for": "alert", "name": name, "until": want})


def _page_run_state() -> dict:
    import json as _json
    import urllib.request

    token = wait.UI_TOKEN.read_text().strip()
    req = urllib.request.Request(f"http://127.0.0.1:{os.environ.get('BENCHCTL_UI_PORT', '8088')}/api/scenarios/run", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return _json.load(r)


@wait_app.command("scenario")
def wait_scenario(name: str = typer.Argument(..., autocompletion=completion.scenarios, help="a scenario the web page is playing"),
                  timeout: float = TIMEOUT) -> None:
    """Until the web page's run of NAME has ended (exit 1 if it ended failed). Scenarios started with `scenario run` block by themselves. Exit 2 when the page is not running."""
    _wait(wait.scenario_check(name, _page_run_state), timeout, {"for": "scenario", "name": name})


import_app = typer.Typer(cls=SuggestingGroup, help="Bring exported files in: a production cluster's configuration (config lab)")
app.add_typer(import_app, name="import")


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
app.add_typer(diff_app, name="diff")
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


@remove_app.command("lab")
def remove_lab(name: str = typer.Argument(..., help="the lab that is applied"),
               execute: bool = typer.Option(False, "--execute", hidden=True, help="run here, not through the governor (the governor's own child)")) -> None:
    """Return to the plain sim.yaml cluster through the governor: restores the pre-lab accounting, deletes what the lab made, puts slurm.conf back. The imported lab stays on disk."""
    _lab_run("remove", name, "baseline", execute)


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


app.add_typer(commands.sshkey.ssh_key_app, name="ssh-key")


@app.command()
def scale(n: int = typer.Argument(..., min=0, help="compute node count"),
          profile: str = typer.Option(None, "--profile", autocompletion=completion.profiles, callback=_profile_check, help="default: the profile `up` last started"),
          dry_run: bool = typer.Option(False, "-n", "--dry-run", help="say which nodes start or stop; change nothing"),
          yes: bool = YES) -> None:
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


app.add_typer(commands.scenario.scenario_app, name="scenario")
app.add_typer(commands.measure.measure_app, name="measure")


if __name__ == "__main__":
    app(prog_name="benchctl")
