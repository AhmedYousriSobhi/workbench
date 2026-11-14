"""One Prometheus target per simulated node, generated from the selection (issue 458, ADR 0073). Run:
`./scripts/verify.sh --unit prom_targets`. The Kubernetes and Teleport jobs are checked in their own live modules
(test_k8s_real, test_teleport_real), which already run those parts."""
import json
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, exporter_up, prom_up, release, wait_for

ROOT = Path(__file__).resolve().parents[2]
EXPORTER = "http://127.0.0.1:9400"


def post(url: str, body: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


@pytest.fixture(scope="module")
def live():
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(exporter_up, timeout=60)
    yield sim
    post(f"{EXPORTER}/sim/inject", {"node": "gpu002", "down": False})  # a shared module leaves the cluster as it found it
    release()


def test_every_node_is_its_own_target_and_answers(live):
    names = ["cn001", "cn002", "cn003", "cn004", "gpu001", "gpu002"]
    assert wait_for(lambda: sorted(prom_up("sim-nodes")) == names and set(prom_up("sim-nodes").values()) == {"1"}, timeout=90), prom_up("sim-nodes")


def test_the_generated_files_are_where_prometheus_reads_them(live):
    targets = json.loads((ROOT / "compose/generated/prometheus/targets/sim-nodes.json").read_text())
    assert len(targets) == 6 and targets[0]["labels"] == {"job": "sim-nodes", "instance": "cn001", "__param_node": "cn001"}
    head = (ROOT / "compose/generated/prometheus/scrape-parts.yml").read_text().splitlines()[0]
    assert head.startswith("# GENERATED")


def test_a_powered_off_node_is_down_alone_and_comes_back(live):
    post(f"{EXPORTER}/sim/inject", {"node": "gpu002", "down": True})
    assert wait_for(lambda: prom_up("sim-nodes").get("gpu002") == "0", timeout=60), prom_up("sim-nodes")
    assert prom_up("sim-nodes").get("gpu001") == "1"  # only that node's target fails
    post(f"{EXPORTER}/sim/inject", {"node": "gpu002", "down": False})
    assert wait_for(lambda: prom_up("sim-nodes").get("gpu002") == "1", timeout=60)
