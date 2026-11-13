"""`benchctl serve mcp` (SPEC AG-4, ADR 0064). The MCP package is the optional [mcp] extra, so these tests skip without it
(CI installs it); `tests/test_mcp_isolation.py` runs everywhere."""
import asyncio

import pytest

pytest.importorskip("mcp")
from benchctl import mcp_server

TOOLS = {
    "workbench_describe": ({}, ["describe", "workbench", "--json"]),
    "workbench_plan": ({"cpu_nodes": 2, "with_parts": "jira"}, ["plan", "--json", "--cpu-nodes", "2", "--with", "jira"]),
    "workbench_up": ({"profile": "std"}, ["up", "--profile", "std"]),
    "workbench_down": ({}, ["down", "--yes"]),
    "scenario_list": ({}, ["scenario", "list", "--json"]),
    "scenario_run": ({"name": "node-down", "dry_run": True}, ["scenario", "run", "node-down", "--speed", "1.0", "--json", "--dry-run"]),
    "faults_get": ({}, ["get", "faults", "--json"]),
    "endpoints_get": ({}, ["get", "endpoints", "--json"]),
    "fault_inject": ({"node": "cn001", "state": "drain", "reason": "t"}, ["set", "node", "cn001", "drain", "--reason", "t"]),
    "traffic_get": ({"since": 5.0}, ["get", "traffic", "--json", "--since", "5.0"]),
    "alerts_get": ({}, ["get", "alerts", "--json"]),
    "wait": ({"kind": "node-state", "name": "cn001", "state": "running", "timeout": 5}, ["wait", "node-state", "cn001", "running", "--timeout", "5.0"]),
}
READ_ONLY = {"workbench_describe", "workbench_plan", "scenario_list", "faults_get", "endpoints_get", "traffic_get", "alerts_get", "wait"}
DESTRUCTIVE = {"workbench_down", "fault_inject"}


def server(calls):
    return mcp_server.build(lambda args, **kw: calls.append(args) or {"ok": True})


def test_the_tools_and_their_annotations():
    tools = {t.name: t for t in asyncio.run(server([]).list_tools())}
    assert set(tools) == set(TOOLS)
    for name, t in tools.items():
        assert t.description, name
        assert t.annotations.read_only_hint is (name in READ_ONLY), name
        assert t.annotations.destructive_hint is (name in DESTRUCTIVE), name


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_each_tool_runs_its_benchctl_command(name):
    args, argv = TOOLS[name]
    calls = []
    asyncio.run(server(calls).call_tool(name, args))
    assert calls == [argv]


def test_wait_with_an_unknown_kind_is_a_usage_error_not_a_call():
    calls = []
    res = asyncio.run(server(calls).call_tool("wait", {"kind": "bogus"}))
    assert calls == [] and "WB-USAGE" in res.content[0].text


def test_resources_serve_the_agent_docs_and_cannot_escape():
    s = server([])
    agents = asyncio.run(s.read_resource("workbench://agents"))
    assert "AGENTS.md" in agents[0].content
    recipe = asyncio.run(s.read_resource("workbench://recipes/node-down"))[0].content
    assert recipe.startswith("# Recipe: node-down")
    assert "no recipe" in asyncio.run(s.read_resource("workbench://recipes/nope"))[0].content
    with pytest.raises(Exception, match="Unknown resource"):  # the SDK itself refuses a path-traversal name
        asyncio.run(s.read_resource("workbench://recipes/..%2F..%2FCLAUDE"))
    assert '"contract_version"' in asyncio.run(s.read_resource("workbench://schemas/get-nodes"))[0].content


def test_run_cli_is_the_real_cli_in_agent_mode():
    data = mcp_server.run_cli(["get", "profiles", "--json"])
    assert data["exit_code"] == 0 and data["contract_version"] and data["items"]
    refused = mcp_server.run_cli(["remove", "jobs"])  # no --yes: agent mode refuses before anything happens
    assert refused["exit_code"] == 1 and "WB-NEEDS-YES" in refused["error"]
