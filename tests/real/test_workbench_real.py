"""A workbench chosen by counts is what actually runs: nodes, Slurm, BMC, GPU exporter and the export agree (#92)."""
import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest
from helpers import wait_for

from benchctl import stack, workbench

ROOT = Path(__file__).resolve().parents[2]


def benchctl(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts/benchctl"), *args], capture_output=True, text=True, timeout=timeout, check=False)


@pytest.fixture(scope="module")
def built():
    os.chdir(ROOT)
    out = benchctl("up", "--cpu-nodes", "2", "--gpu-nodes", "3", "--gpus-per-node", "4", "--with", "jira", "--without", "bcm")
    assert out.returncode == 0, out.stdout + out.stderr
    assert wait_for(lambda: "gpu003" in benchctl("exec", "login", "--", "sinfo", "-h", "-N", "-o", "%N", timeout=60).stdout, timeout=90)
    yield out.stdout
    stack.down(say=lambda m: None)
    workbench.forget()


def test_plan_printed_before_the_build_and_the_build_was_admitted(built):
    assert "2 cpu, 3 gpu x 4 GPUs" in built and "admitted:" in built and "stack is up" in built


def test_slurm_and_get_nodes_show_exactly_the_chosen_nodes(built):
    sinfo = benchctl("exec", "login", "--", "sinfo", "-h", "-N", "-o", "%N", timeout=60).stdout.split()
    assert sorted(set(sinfo)) == ["cn001", "cn002", "gpu001", "gpu002", "gpu003"]
    rows = json.loads(benchctl("get", "nodes", "-o", "json", timeout=60).stdout)
    assert [r["name"] for r in rows] == ["cn001", "cn002", "gpu001", "gpu002", "gpu003"]
    assert all(r["state"] == "running" for r in rows)


def test_gpu_exporter_and_bmc_follow_the_selection(built):
    metrics = urllib.request.urlopen("http://127.0.0.1:9400/metrics", timeout=10).read().decode()
    assert 'node="gpu003"' in metrics and 'node="gpu004"' not in metrics
    assert metrics.count('DCGM_FI_DEV_GPU_UTIL{node="gpu003"') == 4  # --gpus-per-node 4
    assert urllib.request.urlopen("http://127.0.0.1:8000/cn002/redfish/v1/Systems/1", timeout=10).status == 200
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen("http://127.0.0.1:8000/cn003/redfish/v1/Systems/1", timeout=10)


def test_unselected_part_is_not_running_and_export_agrees(built):
    services = {r["name"] for r in json.loads(benchctl("get", "services", "-o", "json", timeout=60).stdout)}
    assert "jira-mock" in services and "bcm-mock" not in services
    body = json.loads(benchctl("export", "profile", "--bmc", "--format", "demo", "-o", "-", timeout=60).stdout)
    assert [t["node"] for t in body["bmc"]] == ["cn001", "cn002", "gpu001", "gpu002", "gpu003"]
    assert body["clusters"][0]["jira"]["projectKey"] == "OPS"


def test_describe_sees_the_four_gpus_of_a_chosen_node(built):
    text = benchctl("describe", "node", "gpu003", timeout=60).stdout
    assert "4 x h200" in text and "Container:   running" in text
