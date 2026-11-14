"""A real MCP client over stdio against a spawned `benchctl serve mcp` (issue 638): the handshake, the tool list with its annotations,
and three calls (`workbench_describe`, `scenario_run` with `dry_run`, `wait`). This is the SDK's own client and the real server
process; an agent runtime (a client that asks the person before a destructive tool) is a manual check, see docs/agents/README.md.
Skips without the optional [mcp] extra."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def session_calls():
    params = StdioServerParameters(command=sys.executable, args=["-m", "benchctl.cli", "serve", "mcp"], cwd=str(ROOT))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        tools = (await s.list_tools()).tools
        described = await s.call_tool("workbench_describe", {})
        dry = await s.call_tool("scenario_run", {"name": "node-down", "dry_run": True})
        waited = await s.call_tool("wait", {"kind": "alert", "name": "NodeDown", "timeout": 1})
        return tools, described, dry, waited


def payload(result) -> dict:
    return json.loads(result.content[0].text)


def test_a_stdio_client_lists_the_tools_and_calls_three_of_them():
    tools, described, dry, waited = asyncio.run(asyncio.wait_for(session_calls(), timeout=120))
    by_name = {t.name: t for t in tools}
    assert len(by_name) == 15 and {"scenario_status", "fault_clear", "capture_once"} <= set(by_name)
    # what a client uses to decide whether to ask the person first
    assert by_name["workbench_down"].annotations.destructive_hint is True and by_name["fault_inject"].annotations.destructive_hint is True
    assert all(t.annotations.read_only_hint for n, t in by_name.items() if n.endswith("_get") or n in ("workbench_describe", "scenario_status", "capture_once"))
    assert by_name["fault_clear"].annotations.destructive_hint is False
    d = payload(described)
    assert d["contract_version"] and "endpoints" in d and "next" in d
    run = payload(dry)
    assert run["dry_run"] is True and run["timeline"][0]["target"] == "bmc-farm"
    w = payload(waited)  # no Prometheus in a unit run: a precondition, answered as data, not a protocol error
    assert w["outcome"] == "precondition" and w["exit_code"] == 2 and not waited.is_error
