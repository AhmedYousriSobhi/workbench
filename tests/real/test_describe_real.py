"""describe node against the live stack: a Slurm job shows up with its busy GPUs, and the page serves the same text (#93)."""
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import stack

ROOT = Path(__file__).resolve().parents[2]


def describe(node: str) -> str:
    return subprocess.run([str(ROOT / "scripts/benchctl"), "describe", "node", node], capture_output=True, text=True,
                          timeout=60, check=False).stdout


def _exporter_up() -> bool:
    try:
        return b"DCGM_FI_DEV_GPU_UTIL" in urllib.request.urlopen("http://127.0.0.1:9400/metrics", timeout=3).read()
    except OSError:
        return False


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: "gpu001" in subprocess.run(
        ["docker", "exec", "-u", "alice", "workbench-login-1", "sinfo", "-h", "-N", "-p", "gpu", "-t", "idle", "-o", "%N"],
        capture_output=True, text=True, check=False).stdout, timeout=90)
    assert wait_for(_exporter_up, timeout=120), "the GPU exporter (it installs pyyaml at start) never answered"
    yield
    stack.down(say=lambda m: None)


def test_describe_shows_container_slurm_bmc_and_gpus(cluster):
    text = describe("gpu001")
    for want in ("Name:        gpu001", "Container:   running", "memory limit", "State:", "8 x h200", "power On", "ssh gpu001"):
        assert want in text, text


def test_a_running_job_appears_with_its_busy_gpus(cluster):
    r = subprocess.run(["docker", "exec", "-u", "alice", "workbench-login-1", "sbatch", "-D", "/tmp", "--wrap=sleep 120", "-p", "gpu",
                        "--nodelist=gpu001", "--gres=gpu:2"], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert wait_for(lambda: "(alice, gpu, RUNNING)" in describe("gpu001") and "busy [0, 1]" in describe("gpu001"), timeout=60), describe("gpu001")
