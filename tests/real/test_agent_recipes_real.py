"""The agent recipes played against the live min stack (ADR 0063, issues 634 and 633): `gpu-fault-to-alert`, `drain-to-ticket`,
`what-did-my-app-call` and `node-down`, each through the same `benchctl` commands the recipe shows, plus the agent verbs of
issue 635 (`clear faults`, `get capture --once`). Run: `./scripts/verify.sh --unit agent_recipes`.

No monitoring app is connected, so the scenarios' expectations that need one (`jira.issue_created`) are not asserted; what the
recipe says the workbench shows (faults, alerts, ticket moves in the Jira mock, the traffic record) is."""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, exporter_up, release, wait_for

ROOT = Path(__file__).resolve().parents[2]
JIRA = "http://127.0.0.1:8080"


def benchctl(*args: str, timeout: float = 240) -> subprocess.CompletedProcess:
    """One call as an agent makes it: agent mode, JSON on stdout."""
    return subprocess.run([sys.executable, "-m", "benchctl.cli", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "BENCHCTL_AGENT": "1"}, check=False)


def background(*args: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "benchctl.cli", *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={**os.environ, "BENCHCTL_AGENT": "1"})


def faults() -> list[dict]:
    r = benchctl("get", "faults", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["items"]


def prometheus_up() -> bool:
    try:
        return urllib.request.urlopen("http://127.0.0.1:9090/-/ready", timeout=3).status == 200
    except OSError:
        return False


@pytest.fixture(scope="module")
def live():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(exporter_up, timeout=60)
    assert wait_for(prometheus_up, timeout=60)
    yield sim
    benchctl("clear", "faults")  # a shared module leaves the cluster as it found it
    release()


def finish(proc: subprocess.Popen, timeout: float = 240) -> dict:
    out, err = proc.communicate(timeout=timeout)
    try:
        return {"code": proc.returncode, **json.loads(out)}
    except ValueError:
        pytest.fail(f"no JSON from the scenario run (exit {proc.returncode}): {out[-300:]} {err[-300:]}")


def test_gpu_fault_to_alert_the_default_rules_fire_and_resolve(live):
    """The recipe: play gpu-xid-storm, `wait alert GpuXid`, `get faults`, `get alerts`. Sped up 3x (the 3 minute timeline takes one)."""
    play = background("scenario", "run", "gpu-xid-storm", "--speed", "3", "--json")
    try:
        fired = benchctl("wait", "alert", "GpuXid", "--timeout", "90")
        assert fired.returncode == 0, fired.stdout + fired.stderr
        assert json.loads(fired.stdout)["outcome"] == "met"
        assert {f["target"] for f in faults()} >= {"gpu001", "gpu002"}
        alerts = json.loads(benchctl("get", "alerts", "--json").stdout)["items"]
        assert "GpuXid" in {a["name"] for a in alerts if a["state"] == "firing"}
        assert {a["labels"]["node"] for a in alerts if a["name"] == "GpuXid"} >= {"gpu001", "gpu002"}
    finally:
        report = finish(play)
    assert all(s["status"] == "ok" for s in report["steps"]), report["steps"]  # the expect that needs a ticket-filing app is not asserted
    resolved = benchctl("wait", "alert", "GpuXid", "--resolved", "--timeout", "120")
    assert resolved.returncode == 0, resolved.stdout + resolved.stderr
    assert not [f for f in faults() if f["target"] in ("gpu001", "gpu002")]


def test_drain_to_ticket_the_mock_shows_the_ticket_moves(live):
    """The recipe: play drain-ticket (sped up), then read who called the Jira mock."""
    r = benchctl("scenario", "run", "drain-ticket", "--speed", "4", "--json")
    data = json.loads(r.stdout)
    assert r.returncode == 0 and data["passed"] is True, r.stdout[-600:] + r.stderr[-300:]
    assert [e["status"] for e in data["expects"]] == ["ok", "ok"]
    traffic = json.loads(benchctl("get", "traffic", "--json").stdout)
    assert traffic["services"]["jira-mock"] is True and any(s["service"] == "jira-mock" for s in traffic["streams"])


def test_what_did_my_app_call_records_the_caller_and_the_status_class(live):
    """The recipe: the app calls a mock; `get traffic --json` names it and how each call ended; `--since` trims `recent`, not `streams`."""
    for path in ("/rest/api/2/search?jql=project%3DOPS", "/rest/api/2/no-such-thing"):
        try:
            urllib.request.urlopen(urllib.request.Request(JIRA + path, headers={"User-Agent": "RecipeApp/9.9"}), timeout=5).read()
        except OSError:
            pass  # the 404 is the point
    data = json.loads(benchctl("get", "traffic", "--json").stdout)
    mine = [s for s in data["streams"] if s["who"] == "recipeapp"]
    assert sum(s["n"] for s in mine) >= 2 and sum(s["client_error"] for s in mine) >= 1
    assert "9.9" not in json.dumps(data)  # the product token only
    later = json.loads(benchctl("get", "traffic", "--since", str(data["now"] + 3600), "--json").stdout)
    assert later["recent"] == [] and {s["shape"] for s in later["streams"]} >= {s["shape"] for s in mine}  # streams stay cumulative


def test_node_down_is_a_fault_while_it_plays_and_gone_after(live):
    """The recipe: run node-down, read `get faults` from a second call while it plays, nothing for cn002 afterwards."""
    play = background("scenario", "run", "node-down", "--speed", "2", "--json")
    try:
        assert wait_for(lambda: any(f["target"] == "cn002" for f in faults()), timeout=30), "cn002 never showed as a fault"
    finally:
        report = finish(play)
    assert report["passed"] is True and [e["status"] for e in report["expects"]] == ["ok"]
    assert wait_for(lambda: not any(f["target"] == "cn002" for f in faults()), timeout=30)


def test_node_down_alert_fires_for_a_powered_off_gpu_node(live):
    """The default NodeDown rule (issue 633): a powered-off GPU node publishes no metrics, so its series vanish; powering it on resolves it
    only after the rule's 3 minute look-back, so this asserts the firing alone."""
    assert benchctl("set", "node", "gpu002", "off").returncode == 0
    try:
        r = benchctl("wait", "alert", "NodeDown", "--timeout", "90")
        assert r.returncode == 0, r.stdout + r.stderr
        assert any(a["labels"].get("node") == "gpu002" for a in json.loads(benchctl("get", "alerts", "--json").stdout)["items"] if a["name"] == "NodeDown")
    finally:
        benchctl("set", "node", "gpu002", "on")


def test_clear_faults_puts_every_node_back(live):
    """Issue 635: inject two faults, one `clear faults` call, `get faults` is empty of nodes."""
    from benchctl import scenario
    for step in (scenario.Step(0, "gpu-exporter", {"nodes": ["gpu001"], "xid": 79}), scenario.Step(0, "bmc-farm", {"nodes": ["cn003"], "temp": 92})):
        assert scenario.execute(step, host=True).status == "ok"
    assert {f["target"] for f in faults()} >= {"gpu001", "cn003"}
    r = benchctl("clear", "faults", "--json")
    data = json.loads(r.stdout)
    assert r.returncode == 0 and data["ok"] is True and {c["target"] for c in data["cleared"]} == {"bmc-farm", "gpu-exporter"}
    assert wait_for(lambda: not [f for f in faults() if f["kind"] in ("compute", "gpu")], timeout=30), faults()


def test_capture_once_is_bounded_and_one_object(live):
    """Issue 635: one answer, at most --seconds, never a body; the Slurm node pings slurmctld often enough to show a message."""
    r = benchctl("get", "capture", "cn001", "--once", "--seconds", "20", "--max-messages", "5", timeout=90)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["node"] == "cn001" and data["count"] == len(data["messages"]) <= 5
    assert data["truncated"] is (data["count"] >= 5)
    assert all("body" not in m for m in data["messages"])
    assert benchctl("get", "capture", "cn001", "--once", "--seconds", "61").returncode == 2
