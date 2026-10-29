"""The scenario engine against the live min stack: each target really changes state, and the shipped node-down runs clean."""
import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from simctl import scenario, stack

ROOT = Path(__file__).resolve().parents[2]


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode()


def ready() -> bool:
    try:
        return all(get(u) for u in ("http://127.0.0.1:8000/healthz", "http://127.0.0.1:8081/healthz",
                                    "http://127.0.0.1:8080/healthz", "http://127.0.0.1:9400/metrics"))
    except OSError:
        return False


def slurm_state(node: str) -> str:
    return scenario.node_state(node)


@pytest.fixture(scope="module")
def live():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(ready, timeout=40)
    assert wait_for(lambda: slurm_state("cn003") != "", timeout=40)
    yield sim
    stack.down(say=lambda m: None)


def test_each_target_changes_real_state(live):
    sc = {"name": "all-targets", "steps": [
        {"at": "0s", "target": "bmc-farm", "do": {"nodes": ["cn004"], "power": "Off"}},
        {"at": "0s", "target": "bcm-mock", "do": {"nodes": ["cn004"], "state": "CLOSED"}},
        {"at": "0s", "target": "gpu-exporter", "do": {"nodes": ["gpu002"], "xid": 79}},
        {"at": "0s", "target": "slurm", "do": {"drain": ["cn003"], "reason": "live test"}},
        {"at": "0s", "target": "jira", "do": {"failure": {"mode": "429", "count": 1}}},
        {"at": "0s", "target": "k8s", "do": {"kwok_node": "x", "ready": False}},
    ], "expect": [{"slurm.node_state": {"node": "cn003", "state": "drain", "within": "20s"}}]}
    rep = scenario.run(sc, say=lambda m: None)
    assert rep.ok, rep
    assert [o.status for o in rep.steps] == ["ok"] * 5 + ["skipped"]
    assert json.loads(get("http://127.0.0.1:8000/cn004/redfish/v1/Systems/1"))["PowerState"] == "Off"
    bcm = urllib.request.Request("http://127.0.0.1:8081/json", method="POST",
                                 data=json.dumps({"service": "cmstatus", "call": "getStatus", "args": ["cn004"]}).encode())
    assert json.loads(urllib.request.urlopen(bcm, timeout=10).read())["result"]["state"] == "CLOSED"
    assert 'xid' in get("http://127.0.0.1:9400/metrics").lower()
    with pytest.raises(urllib.error.HTTPError) as e:
        get("http://127.0.0.1:8080/rest/api/2/issue/X-1")
    assert e.value.code == 429
    for node in ("cn004",):  # leave the mocks as we found them
        scenario.post("http://127.0.0.1:8000/sim/inject", {"node": node, "clear": True})
        scenario.post("http://127.0.0.1:8081/sim/inject", {"node": node, "state": "UP"})
    scenario.post("http://127.0.0.1:9400/sim/inject", {"node": "gpu002", "clear": True})
    subprocess.run(["docker", "exec", scenario.SLURMCTLD, "scontrol", "update", "nodename=cn003", "state=resume"], check=True)


def test_shipped_node_down_runs_fast_forwarded_and_recovers(live):
    rep = scenario.run(scenario.load("node-down"), speed=20, say=lambda m: None)
    assert rep.ok, rep
    assert wait_for(lambda: "drain" not in slurm_state("cn002"), timeout=20)  # the scenario's own resume step
    assert json.loads(get("http://127.0.0.1:8000/cn002/redfish/v1/Systems/1"))["PowerState"] == "On"


def test_cli_dry_run_and_unknown_scenario(live):
    ok = subprocess.run([str(ROOT / "scripts/simctl"), "scenario", "run", "node-down", "--dry-run"], capture_output=True, text=True, check=False)
    assert ok.returncode == 0 and "drain" in ok.stdout
    bad = subprocess.run([str(ROOT / "scripts/simctl"), "scenario", "run", "nope"], capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and "no scenario" in bad.stdout
