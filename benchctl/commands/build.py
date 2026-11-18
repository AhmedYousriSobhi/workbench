"""`benchctl plan|up|apply` and the selection flags they share (parts, node counts, login modes)."""

import os

import typer

from .. import checks, completion, contract, logins, memory, nodes, stack, workbench
from .base import JSON, OUTPUT, _base_sim, _profile_check
from .groups import app
from .lab_support import _lab_run


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
