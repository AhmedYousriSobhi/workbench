"""`benchctl set ...` and `benchctl clear ...`: change one thing on the running cluster, put injected faults back."""

import typer

from .. import agentverbs, contract, netemfaults, nhc, nodes, scenario, stack
from .base import (
    CHECK,
    JSON,
    OUTPUT,
    POSITION,
    SETS,
    TARGET,
    SuggestingGroup,
    _daemon,
    _nhc_done,
    _nhc_run,
    _sim,
)

set_app = typer.Typer(cls=SuggestingGroup, help="Change one thing on the running cluster")


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
    why = reason if reason and state in ("down", "drain") else "workbench power off"  # off/on and resume keep the default text
    slurm_only = scenario.Step(0, "slurm", {keys[state]: [node], "reason": why})
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
    from .. import workload
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


@set_app.command("k8s-step")
def set_k8s_step(do: str = typer.Argument(..., help="a scenario's `k8s` step as JSON, e.g. '{\"down\": [\"kcn001\"]}'")) -> None:
    """`benchctl set k8s-step '{"down": ["kcn001"]}'` plays one Kubernetes scenario step now (node down, up, cordon, uncordon, pod, delete_pod, scale,
    drain and the rest of the `k8s` keys): the page asks the governor for it because the page has no Docker (issue 386). It is checked
    against this cluster's Kubernetes nodes before anything runs."""
    import json

    from .. import k8s

    try:
        step = json.loads(do)
    except ValueError as e:
        typer.echo(f"not JSON: {e}", err=True)
        raise typer.Exit(1) from e
    if not isinstance(step, dict):
        typer.echo("a k8s step is a JSON object", err=True)
        raise typer.Exit(1)
    sim = k8s.current_sim()
    if bad := k8s.step_problems(step, {n for n, _ in k8s.node_list(sim)}):
        typer.echo("\n".join(bad), err=True)
        raise typer.Exit(1)
    o = scenario.execute(scenario.Step(0, "k8s", step), host=True, runner=lambda c: stack._run(c))
    typer.echo("k8s step done" if o.status == "ok" else f"cannot play the k8s step: {o.detail}", err=o.status != "ok")
    raise typer.Exit(0 if o.status == "ok" else 1)


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
