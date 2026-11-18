"""`benchctl describe node|workbench`."""

import typer

from .. import checks, contract, describe, inventory, scenario, situation, stack, workbench
from .base import JSON, NODE, OUTPUT, SuggestingGroup, _base_sim, _sim
from .get import _fault_rows

describe_app = typer.Typer(cls=SuggestingGroup, help="Everything known about one thing")


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
