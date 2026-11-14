"""`benchctl serve mcp`: the workbench as typed tools over MCP, stdio (SPEC AG-4, ADR 0064).

Imported only by `serve mcp` (never by the web page, the governor or plain `benchctl`), because the MCP SDK brings pydantic.
Every tool runs the same `benchctl` command a person would, with `BENCHCTL_AGENT=1`, so the governor's admission and levels
(GV-8, GV-9) and the `--yes` safety rule apply unchanged and no privilege is added. A read tool returns the data of its
`--json` twin. Tools that stop or change things carry the destructive annotation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parent.parent
LOG_TAIL = 2000  # characters of plain output returned for a verb that prints text


def run_cli(args: list[str], timeout: float = 900) -> dict:
    """One benchctl call, as an agent would make it. Returns the parsed JSON, or {"exit_code", "output"} for text."""
    p = subprocess.run([sys.executable, "-m", "benchctl.cli", *args], capture_output=True, text=True, cwd=ROOT, timeout=timeout,
                       env={**os.environ, "BENCHCTL_AGENT": "1"}, check=False)
    try:
        data = json.loads(p.stdout)
        if isinstance(data, dict):
            return {**data, "exit_code": p.returncode, **({"error": p.stderr.strip()} if p.stderr.strip() else {})}
    except ValueError:
        pass
    out = {"exit_code": p.returncode, "output": p.stdout[-LOG_TAIL:]}
    if p.stderr.strip():
        out["error"] = p.stderr.strip()[-LOG_TAIL:]
    return out


READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)


def build(run: Callable[..., dict] = run_cli) -> MCPServer:
    s = MCPServer("workbench", instructions="Test target for a monitoring client. Start with workbench_describe; read AGENTS.md (resource workbench://agents). "
                  "REFUSED answers are the memory governor's decision: free memory or pick a smaller profile, do not retry in a loop.")

    @s.tool(description="One call for situational awareness: state, clusters, endpoints (credentials are file paths), active faults, scenarios, next steps.", annotations=READ)
    def workbench_describe() -> dict:
        return run(["describe", "workbench", "--json"])

    @s.tool(description="Would a selection fit in free memory? Nothing starts. Counts default to the saved selection.", annotations=READ)
    def workbench_plan(cpu_nodes: int | None = None, gpu_nodes: int | None = None, with_parts: str | None = None) -> dict:
        args = ["plan", "--json"]
        for flag, v in (("--cpu-nodes", cpu_nodes), ("--gpu-nodes", gpu_nodes), ("--with", with_parts)):
            if v is not None:
                args += [flag, str(v)]
        return run(args)

    @s.tool(description="Start the workbench (a size preset, default min). Admission can refuse: that is the governor protecting the machine.", annotations=WRITE)
    def workbench_up(profile: str = "min") -> dict:
        return run(["up", "--profile", profile])

    @s.tool(description="Stop and remove the workbench containers (only this project). Destructive: open shells and jobs end.", annotations=DESTRUCTIVE)
    def workbench_down() -> dict:
        return run(["down", "--yes"])

    @s.tool(description="Every scenario: reference, name, step count, description.", annotations=READ)
    def scenario_list() -> dict:
        return run(["scenario", "list", "--json"])

    @s.tool(description="Play a named scenario against the running stack and return every step and expect verdict. Blocks until it ends; set dry_run to only see the timeline.",
            annotations=WRITE)
    def scenario_run(name: str, speed: float = 1.0, dry_run: bool = False) -> dict:
        return run(["scenario", "run", name, "--speed", str(speed), "--json", *(["--dry-run"] if dry_run else [])])

    @s.tool(description="Nodes and services that are not healthy right now (including expired certificates and tokens, a Jira failure mode).", annotations=READ)
    def faults_get() -> dict:
        return run(["get", "faults", "--json"])

    @s.tool(description="Every address a client can reach, with the command that works and the file that holds its credential.", annotations=READ)
    def endpoints_get() -> dict:
        return run(["get", "endpoints", "--json"])

    @s.tool(description="Set a node's state: off or on (power, like the page's button), down, drain or resume (Slurm). Faults a scenario would play, one node at a time.",
            annotations=DESTRUCTIVE)
    def fault_inject(node: str, state: str, reason: str | None = None) -> dict:
        return run(["set", "node", node, state, *(["--reason", reason] if reason else [])])

    @s.tool(description="Who called which mock, how each call ended and how long it took. since: epoch seconds, keeps only newer recent requests.", annotations=READ)
    def traffic_get(since: float = 0.0) -> dict:
        return run(["get", "traffic", "--json", "--since", str(since)])

    @s.tool(description="The alerts Prometheus is pending or firing. The workbench ships three default rules (GpuXid, GpuOverheat, NodeDown); an empty list means none is firing.", annotations=READ)
    def alerts_get() -> dict:
        return run(["get", "alerts", "--json"])

    @s.tool(description="The state of the web page's scenario run: running or not, which one, each step's status and the expect verdicts so far. "
                        "A scenario started with scenario_run is not shown (that call blocks and returns its own report). Needs the web page.", annotations=READ)
    def scenario_status() -> dict:
        return run(["get", "scenario-status", "--json"])

    @s.tool(description="Clear every injected node fault at once (hot BMC, failed fans, GPU XID, InfiniBand or NIC trouble; powered-off nodes are powered on), like the page's Clear faults. "
                        "Service and credential faults are not touched. Safe to repeat.", annotations=WRITE)
    def fault_clear() -> dict:
        return run(["clear", "faults", "--json"])

    @s.tool(description="The messages on one node's wire for a bounded time (Slurm messages, or Kubernetes API events for a Kubernetes node): type, direction, size, timing, never a body. "
                        "Stops at seconds (default 10, at most 60) or max_messages (default 200), whichever comes first. The governor can refuse it at memory level L2 and above.", annotations=READ)
    def capture_once(node: str, seconds: int = 10, max_messages: int = 200) -> dict:
        return run(["get", "capture", node, "--once", "--seconds", str(seconds), "--max-messages", str(max_messages)], timeout=seconds + 60)

    @s.tool(description="Block until something is true, one call instead of polling. kind: ready | node-state (name=node, state) | alert (name, resolved) | scenario (name). "
                        "outcome met, timeout, failed or precondition (a precondition needs a part that is not running).", annotations=READ)
    def wait(kind: str, name: str | None = None, state: str | None = None, resolved: bool = False, timeout: float = 120.0) -> dict:
        args = {"ready": ["ready"], "scenario": ["scenario", name or ""], "alert": ["alert", name or "", *(["--resolved"] if resolved else [])],
                "node-state": ["node-state", name or "", state or ""]}.get(kind)
        if args is None:
            return {"exit_code": 2, "error": "WB-USAGE: kind is ready, node-state, alert or scenario | fix: benchctl wait --help"}
        return run(["wait", *args, "--timeout", str(timeout)], timeout=timeout + 60)

    def text(path: Path) -> str:
        return path.read_text()

    @s.resource("workbench://agents", mime_type="text/markdown")
    def agents_md() -> str:
        return text(ROOT / "AGENTS.md")

    @s.resource("workbench://recipes/{name}", mime_type="text/markdown")
    def recipe(name: str) -> str:
        path = ROOT / "docs" / "agents" / "recipes" / f"{Path(name).name}.md"
        return text(path) if path.is_file() else f"no recipe {name!r}"

    @s.resource("workbench://schemas/{name}", mime_type="application/json")
    def schema(name: str) -> str:
        path = ROOT / "docs" / "agents" / "schemas" / f"{Path(name).name}.json"
        return text(path) if path.is_file() else json.dumps({"error": f"no schema {name!r}"})

    return s


def serve() -> None:
    build().run("stdio")
