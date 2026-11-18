"""`benchctl get ...`: list what exists (read-only). Also the helpers `describe` and `export` reuse."""

import json
import os
import sys
import time

import typer

from .. import (
    agentmode,
    agentverbs,
    alerts,
    capture,
    completion,
    contract,
    errors,
    faultstate,
    inventory,
    lab,
    nhc,
    slurmstats,
    slurmtrace,
    slurmver,
    stack,
    wait,
    workbench,
)
from .base import JSON, OUTPUT, SuggestingGroup, _base_sim, _sim

SELECTOR = typer.Option(None, "-l", "--selector", autocompletion=completion.selectors, help="keep rows where key=value or key!=value (repeat or comma-separate)")


SORT_BY = typer.Option(None, "--sort-by", autocompletion=completion.columns, help="sort by a column name")


WATCH = typer.Option(False, "-w", "--watch", help="redraw until Ctrl-C")


INTERVAL = typer.Option(2.0, "--interval", help="seconds between redraws with --watch (at least 1)")


get_app = typer.Typer(cls=SuggestingGroup, help="List what exists: nodes, services, profiles, scenarios")


@get_app.command("ticket-bot")
def get_ticket_bot() -> None:
    """The settings the running ticket bot was last given with `benchctl set ticket-bot` (empty: its start values, see `load on --tickets`)."""
    from .. import workload
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


def _page_run_state() -> dict:
    import json as _json
    import urllib.request

    token = wait.UI_TOKEN.read_text().strip()
    req = urllib.request.Request(f"http://127.0.0.1:{os.environ.get('BENCHCTL_UI_PORT', '8088')}/api/scenarios/run", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return _json.load(r)
