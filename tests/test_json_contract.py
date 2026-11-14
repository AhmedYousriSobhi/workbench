"""The JSON contract (SPEC AG-3, AG-9; ADR 0062): every `--json` output validates against its schema, carries a
`contract_version`, equals `-o json`, and holds no secret value. Hermetic: the heavy parts are faked."""
import json
import re
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from benchctl import (
    agentverbs,
    alerts,
    checks,
    cli,
    contract,
    describe,
    faultstate,
    inventory,
    scenario,
    stack,
)
from webui import cluster, traffic

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "docs" / "agents" / "schemas"

# a few values that must never reach any output: a home path, an e-mail, a key block, a JWT, a password assignment
SECRETS = [re.compile(p) for p in (r"/home/[a-z_][a-z0-9_-]*/", r"[\w.+-]+@[\w-]+\.[a-z]{2,}", r"BEGIN [A-Z ]*PRIVATE KEY",
                                   r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", r"(?i)(password|passwd|token)\s*[=:]\s*\S{6,}")]


@pytest.fixture(autouse=True)
def in_repo(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"cn001": "running", "gpu001": "paused"})
    monkeypatch.setattr(checks, "run_all", lambda: [checks.Check("a", checks.OK, "fine"), checks.Check("b", checks.WARN, "meh", fix="do this")])
    monkeypatch.setattr(checks, "mem_available", lambda: 12 * 1024**3)

    def gov(req, socket=None):
        raise OSError("no socket")
    monkeypatch.setattr(checks, "governor_request", gov)
    nodes = [{"name": "cn001", "kind": "compute", "state": "down", "label": "Powered off"}, {"name": "gpu001", "kind": "gpu", "state": "ok", "label": "Healthy"}]
    monkeypatch.setattr(faultstate, "collect", lambda fetch, runner, running: [faultstate.row("jira-mock", "service", "Failure mode 503 for the next 2 request(s)")])
    monkeypatch.setattr(cluster.Cluster, "snapshot", lambda self: {"nodes": nodes})
    sample = {"now": 1.0, "edges": None, "services": {"bmc-farm": True, "bcm-mock": None, "jira-mock": False}, "recent": [],
              "streams": [{"service": "bmc-farm", "who": "client", "shape": "GET /redfish/v1", "n": 3, "ok": 3, "client_error": 0, "server_error": 0, "ms": 1.5, "hist": [3]}]}
    monkeypatch.setattr(traffic, "collect", lambda since=0.0: sample)
    monkeypatch.setattr(stack, "governor_admits", lambda need, say, ask=None: True)
    ran = scenario.Report("node-down", [scenario.Outcome("bmc-farm power", "ok")], [scenario.Outcome("expect node down", "failed", "did not hold")])
    monkeypatch.setattr(scenario, "run", lambda sc, **k: ran)
    monkeypatch.setattr(alerts, "fetch", lambda get=None: [{"name": "NodeDown", "state": "firing", "severity": "page", "labels": {"alertname": "NodeDown"}, "active_at": "2026-11-13T10:00:00Z"}])
    monkeypatch.setattr(cli, "_page_run_state", lambda: {"running": False, "name": "node-down", "ok": True, "steps": [{"at": 0, "target": "bmc-farm", "do": {}, "status": "ok", "detail": ""}], "expects": []})
    monkeypatch.setattr(agentverbs, "clear_faults", lambda sim, post, run: {"ok": True, "cleared": [{"target": "bmc-farm", "nodes": ["cn001"], "status": "ok", "detail": ""}]})
    monkeypatch.setattr(agentverbs.capture, "run", lambda sim, node, emit, seconds, packets=0, stop=None: emit({"t": 1.0, "dir": "out", "kind": "request", "type": "REQUEST_PING"}))
    monkeypatch.setattr(describe, "gather", lambda *a, **k: {"name": "cn001", "kind": "compute", "notes": [], "container": {"state": "running"}})


def schema(name):
    return json.loads((SCHEMAS / f"{name}.json").read_text())


# (schema, argv): every read verb with a JSON contract so far; `describe workbench` and `get alerts` follow in step 11
VERBS = [("get-nodes", ["get", "nodes"]), ("get-services", ["get", "services"]), ("get-profiles", ["get", "profiles"]),
         ("get-checks", ["get", "checks"]), ("get-scenarios", ["get", "scenarios"]), ("scenario-list", ["scenario", "list"]),
         ("get-workbench", ["get", "workbench"]), ("describe-node", ["describe", "node", "cn001"]), ("doctor", ["doctor"]),
         ("status", ["status"]), ("get-alerts", ["get", "alerts"]), ("describe-workbench", ["describe", "workbench"]), ("get-endpoints", ["get", "endpoints"]), ("get-faults", ["get", "faults"]),
         ("get-traffic", ["get", "traffic"]), ("get-scenario-status", ["get", "scenario-status"]), ("clear-faults", ["clear", "faults"]), ("get-capture-once", ["get", "capture", "cn001", "--once"]), ("scenario-run", ["scenario", "run", "node-down"]), ("plan", ["plan", "--cpu-nodes", "2", "--gpu-nodes", "1"])]


@pytest.mark.parametrize(("name", "argv"), VERBS, ids=[v[0] for v in VERBS])
def test_json_output_matches_its_schema_and_o_json(name, argv):
    r = runner.invoke(cli.app, [*argv, "--json"])
    assert r.exit_code in (0, 1), r.output
    data = json.loads(r.stdout)
    jsonschema.validate(data, schema(name), cls=jsonschema.Draft202012Validator)
    assert data["contract_version"] == contract.CONTRACT_VERSION
    assert runner.invoke(cli.app, [*argv, "-o", "json"]).stdout == r.stdout  # `--json` is only an alias
    for pat in SECRETS:
        assert not pat.search(r.stdout), f"{name}: output matches {pat.pattern}"


def test_scenario_run_json_keeps_stdout_to_one_object_and_the_exit_code():
    r = runner.invoke(cli.app, ["scenario", "run", "node-down", "--json"])
    data = json.loads(r.stdout)  # progress lines went to stderr
    assert r.exit_code == 1 and data["passed"] is False and data["expects"][0]["status"] == "failed"


def test_scenario_dry_run_json_is_the_timeline():
    data = json.loads(runner.invoke(cli.app, ["scenario", "run", "node-down", "--dry-run", "--json"]).stdout)
    jsonschema.validate(data, schema("scenario-run"), cls=jsonschema.Draft202012Validator)
    assert data["dry_run"] is True and data["timeline"][0]["target"] == "bmc-farm"


def test_faults_lists_unhealthy_nodes_and_service_faults():
    items = json.loads(runner.invoke(cli.app, ["get", "faults", "--json"]).stdout)["items"]
    assert [(i["target"], i["kind"]) for i in items] == [("cn001", "compute"), ("jira-mock", "service")]


def test_endpoints_give_credentials_as_paths_only():
    items = json.loads(runner.invoke(cli.app, ["get", "endpoints", "--json"]).stdout)["items"]
    assert any(i["credential"].startswith("compose/generated/secrets/") for i in items)
    assert "command" in items[0] and "command" not in runner.invoke(cli.app, ["get", "endpoints"]).stdout


def test_describe_workbench_fits_its_budget_and_holds_no_secret_value():
    from benchctl import situation

    r = runner.invoke(cli.app, ["describe", "workbench", "--json"])
    assert len(r.stdout.strip()) <= situation.MAX_CHARS, len(r.stdout)
    data = json.loads(r.stdout)
    assert data["scenarios"] and data["endpoints"] and data["next"]
    for e in data["endpoints"]:
        assert e.get("credential", "compose/generated/secrets/").startswith("compose/generated/secrets/"), e  # a path, never a value
    assert [f["target"] for f in data["faults_active"]] == ["cn001", "jira-mock"]


def test_wait_result_matches_its_schema(monkeypatch):
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"cn001": "running"})
    for argv in (["wait", "node-state", "cn001", "running"], ["wait", "alert", "NodeDown"]):
        r = runner.invoke(cli.app, [*argv, "--timeout", "1"])
        data = json.loads(r.stdout)
        jsonschema.validate(data, schema("wait"), cls=jsonschema.Draft202012Validator)
        assert r.exit_code == 0 and data["outcome"] == "met", argv


def test_status_reports_a_reachable_governor(monkeypatch):
    st = {"level": 0, "dry_run": False, "mode": "user", "mem_available": 8 * 1024**3, "psi_some": 0.1, "psi_full": 0.0,
          "groups": ["p6"], "audit": ["L0 start"], "violations": []}
    monkeypatch.setattr(checks, "governor_request", lambda req, socket=None: st)
    r = runner.invoke(cli.app, ["status", "--json"])
    data = json.loads(r.stdout)
    jsonschema.validate(data, schema("status"), cls=jsonschema.Draft202012Validator)
    assert r.exit_code == 0 and data["governor"]["reachable"] is True and data["governor"]["mem_available_mb"] == 8192


def test_list_outputs_are_enveloped():
    data = json.loads(runner.invoke(cli.app, ["get", "nodes", "--json"]).stdout)
    assert {"name": "gpu001", "kind": "gpu", "state": "paused"} in data["items"]


def test_every_schema_is_valid_draft_2020_12_and_has_a_verb():
    files = {p.stem for p in SCHEMAS.glob("*.json")}
    for name in files:
        jsonschema.Draft202012Validator.check_schema(schema(name))
        assert schema(name)["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert "contract_version" in schema(name)["required"]
    assert files == {v[0] for v in VERBS} | {"wait"}, "a schema without a test, or a verb without a schema"


def test_the_changelog_names_the_current_version():
    text = (ROOT / "docs" / "agents" / "CHANGELOG.md").read_text()
    assert f"## {contract.CONTRACT_VERSION} (" in text
