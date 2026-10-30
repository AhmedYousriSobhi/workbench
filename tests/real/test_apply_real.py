"""Apply a new architecture through the governor (#117): nodes are removed and added on a live stack."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import ask, bring_up, start_governor, stop_governor, wait_for

from benchctl import stack, workbench

ROOT = Path(__file__).resolve().parents[2]


def services() -> set[str]:
    out = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.project=workbench", "--format",
                          '{{.Label "com.docker.compose.service"}}'], capture_output=True, text=True, check=False).stdout
    return set(out.split())


def slurm_nodes() -> set[str]:
    out = subprocess.run(["docker", "exec", "-u", "alice", "workbench-login-1", "sinfo", "-h", "-N", "-o", "%N"],
                         capture_output=True, text=True, check=False).stdout
    return set(out.split())


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    os.chdir(ROOT)
    tmp = tmp_path_factory.mktemp("gov")
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    proc, _, sock = start_governor(tmp, "--user-mode", "--workdir", str(ROOT), env={"WORKBENCH_GOV_SOCKET": str(tmp / "gov.sock")})
    yield sock
    stop_governor(proc)
    stack.down(say=lambda m: None)
    workbench.forget()  # `benchctl up <selection>` saves its counts; leaving them would change every later test's node list


def test_apply_plan_shrinks_then_grows_the_running_workbench(cluster):
    plan = {"cmd": "apply-plan", "cpu_nodes": 2, "gpu_nodes": 1, "gpus_per_node": 8, "parts": ["jira", "bmc", "bcm", "scenarios"]}
    assert ask(cluster, plan)["started"]
    assert wait_for(lambda: {"cn001", "cn002", "gpu001"} <= services() and not ({"cn003", "cn004", "gpu002"} & services()), timeout=300)
    assert wait_for(lambda: slurm_nodes() == {"cn001", "cn002", "gpu001"}, timeout=120), slurm_nodes()
    assert any("apply-plan" in line and "done" in line for line in ask(cluster, {"cmd": "status"})["audit"])
    assert ask(cluster, {**plan, "cpu_nodes": 3, "gpu_nodes": 2})["started"]
    assert wait_for(lambda: {"cn003", "gpu002"} <= services(), timeout=300)
    # let the second `benchctl up` finish (it saves the spec last) before the fixture stops the governor mid-run
    assert wait_for(lambda: sum("apply-plan" in x and ": done" in x for x in ask(cluster, {"cmd": "status"})["audit"]) >= 2, timeout=300)
