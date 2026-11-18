"""`benchctl wait ready|node-state|alert|scenario`."""

import typer

from .. import completion, contract, errors, inventory, ready, stack, wait
from .base import NODE, SuggestingGroup, _sim
from .get import _page_run_state

wait_app = typer.Typer(cls=SuggestingGroup, help="Block until something is true, then print one JSON result (exit 0 met, 1 timeout, 2 precondition not met)")


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


@wait_app.command("scenario")
def wait_scenario(name: str = typer.Argument(..., autocompletion=completion.scenarios, help="a scenario the web page is playing"),
                  timeout: float = TIMEOUT) -> None:
    """Until the web page's run of NAME has ended (exit 1 if it ended failed). Scenarios started with `scenario run` block by themselves. Exit 2 when the page is not running."""
    _wait(wait.scenario_check(name, _page_run_state), timeout, {"for": "scenario", "name": name})
