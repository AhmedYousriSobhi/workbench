"""GPU metrics follow the scheduler: a job's GPUs read busy while it runs and idle again after it ends (#83)."""
import os
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, exporter_up, wait_for

from benchctl import stack

ROOT = Path(__file__).resolve().parents[2]


def utils(node: str) -> dict[int, float]:
    text = urllib.request.urlopen("http://127.0.0.1:9400/metrics", timeout=5).read().decode()
    return {int(m[1]): float(m[2]) for m in re.finditer(
        rf'DCGM_FI_DEV_GPU_UTIL{{node="{node}",gpu="(\d+)"[^}}]*}} (\S+)', text)}


def slurm(*cmd: str, user: str = "alice") -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-u", user, "workbench-login-1", *cmd], capture_output=True, text=True, timeout=30, check=False)


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: "gpu001" in slurm("sinfo", "-h", "-N", "-p", "gpu", "-t", "idle", "-o", "%N").stdout, timeout=90)
    assert wait_for(exporter_up, timeout=180), "the GPU exporter (it installs pyyaml at start) never answered"
    yield
    stack.down(say=lambda m: None)


def test_a_job_makes_its_gpus_busy_and_ending_it_makes_them_idle(cluster):
    assert wait_for(lambda: max(utils("gpu001").values(), default=99) <= 3, timeout=40), "GPUs idle with no job"
    r = slurm("sbatch", "-D", "/tmp", "--wrap=sleep 300", "-p", "gpu", "--nodelist=gpu001", "--gres=gpu:2")
    assert r.returncode == 0, r.stderr
    assert wait_for(lambda: sorted(g for g, v in utils("gpu001").items() if v >= 70) == [0, 1], timeout=40), utils("gpu001")
    assert all(v <= 3 for g, v in utils("gpu001").items() if g >= 2)
    assert max(utils("gpu002").values()) <= 3  # another node's GPUs stay idle
    assert slurm("scancel", "-u", "alice").returncode == 0
    assert wait_for(lambda: max(utils("gpu001").values()) <= 3, timeout=40), utils("gpu001")
