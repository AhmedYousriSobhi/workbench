"""benchctl against the live stack: get, exec and logs see the real containers (#90)."""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import stack

ROOT = Path(__file__).resolve().parents[2]


def benchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts/benchctl"), *args], capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: "cn001" in benchctl("exec", "login", "--", "sinfo", "-h", "-N", "-o", "%N").stdout, timeout=90)
    yield
    stack.down(say=lambda m: None)


def test_get_nodes_reports_real_state(cluster):
    rows = {r["name"]: r for r in json.loads(benchctl("get", "nodes", "-o", "json").stdout)}
    assert rows["cn001"]["state"] == "running" and rows["gpu001"]["kind"] == "gpu"
    assert "cn001" in benchctl("get", "nodes").stdout


def test_get_services_lists_the_stack(cluster):
    names = {r["name"] for r in json.loads(benchctl("get", "services", "-o", "json").stdout)}
    assert {"slurmctld", "login", "cn001"} <= names


def test_exec_runs_a_command_on_the_node_as_a_user(cluster):
    r = benchctl("exec", "cn001", "-u", "alice", "--", "whoami")
    assert r.returncode == 0 and r.stdout.strip() == "alice", r.stderr
    assert benchctl("exec", "gpu001", "--", "hostname").stdout.strip() == "gpu001"


def test_logs_prints_the_container_log(cluster):
    assert benchctl("logs", "slurmctld", "--tail", "5").returncode == 0


def test_selectors_names_and_dry_run_on_the_live_stack(cluster):
    gpu = benchctl("get", "nodes", "-l", "kind=gpu", "-o", "name").stdout.split()
    assert gpu == ["gpu001", "gpu002"]
    assert benchctl("get", "nodes", "-l", "state=running", "-l", "kind!=gpu", "-o", "name").stdout.split()[0] == "cn001"
    assert benchctl("get", "nodes", "-l", "nope=1").returncode == 2
    dry = benchctl("down", "--dry-run")
    assert dry.returncode == 0 and "would stop and remove" in dry.stdout and "slurmctld" in dry.stdout
    assert "cn001" in benchctl("get", "nodes", "-l", "state=running", "-o", "name").stdout  # the dry run stopped nothing
    shrink = benchctl("scale", "2", "--dry-run")
    assert "would stop and remove: cn003, cn004" in shrink.stdout
