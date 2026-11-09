"""The real DCGM exporter on the mock NVML (issue 426): genuine series, Slurm-driven load, an injected XID on one GPU.
Not run by the agent: `./scripts/verify.sh --unit dcgm --clean`."""
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest
from helpers import wait_for

from benchctl import dcgm, stack, workbench

ROOT = Path(__file__).resolve().parents[2]


def benchctl(*args: str, timeout: int = 1200) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts/benchctl"), *args], capture_output=True, text=True, timeout=timeout, check=False)


def scrape(port: int = dcgm.PORT) -> str:
    return urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()


def value(metric: str, gpu: int) -> float | None:
    m = re.search(rf'^{metric}{{[^}}]*gpu="{gpu}"[^}}]*}} (\S+)', scrape(), re.MULTILINE)
    return float(m[1]) if m else None


def slurm(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-u", "alice", "workbench-login-1", *cmd], capture_output=True, text=True, timeout=30, check=False)


@pytest.fixture(scope="module")
def built():
    os.chdir(ROOT)
    out = benchctl("up", "--cpu-nodes", "1", "--gpu-nodes", "1", "--gpus-per-node", "2", "--with", "dcgm", "--with", "scenarios")
    assert out.returncode == 0, out.stdout + out.stderr
    assert wait_for(lambda: "DCGM_FI_DEV_GPU_UTIL" in scrape(), timeout=120), "the real exporter never answered"
    yield
    stack.down(say=lambda m: None)
    workbench.forget()


def test_the_series_and_labels_are_the_real_exporters(built):
    text = scrape()
    assert 'Hostname="gpu001"' in text and 'modelName="' in text and 'UUID="GPU-' in text and text.count("DCGM_FI_DEV_GPU_UTIL{") == 2
    used, free = value("DCGM_FI_DEV_FB_USED", 0), value("DCGM_FI_DEV_FB_FREE", 0)
    assert used is not None and free is not None and used + free > 40000  # the framebuffer total, in MiB


def test_a_slurm_job_makes_its_gpu_busy_and_the_other_idle(built):
    assert wait_for(lambda: value("DCGM_FI_DEV_GPU_UTIL", 0) == 0, timeout=30)
    r = slurm("sbatch", "-D", "/tmp", "--wrap=sleep 300", "-p", "gpu", "--nodelist=gpu001", "--gres=gpu:1")
    assert r.returncode == 0, r.stderr
    assert wait_for(lambda: (value("DCGM_FI_DEV_GPU_UTIL", 0) or 0) >= 70, timeout=60), scrape()[:400]
    assert value("DCGM_FI_DEV_GPU_UTIL", 1) == 0
    slurm("scancel", "-u", "alice")
    assert wait_for(lambda: value("DCGM_FI_DEV_GPU_UTIL", 0) == 0, timeout=60)


def test_an_injected_xid_shows_on_one_gpu_and_stays_like_real_dcgm(built):
    req = urllib.request.Request("http://127.0.0.1:9400/sim/inject", data=json.dumps({"node": "gpu001", "xid": 63}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    assert urllib.request.urlopen(req, timeout=5).status == 200
    assert wait_for(lambda: value("DCGM_FI_DEV_XID_ERRORS", 0) == 63, timeout=60), scrape()[:400]
    assert value("DCGM_FI_DEV_XID_ERRORS", 1) in (None, 0)
    # real DCGM keeps the last XID until its host engine restarts, so a cleared fault does not reset it here (documented, ADR 0025)


def test_dcgmi_works_in_the_exporter_container_and_only_there(built):
    r = subprocess.run(["docker", "exec", "workbench-dcgm-exporter-1", "dcgmi", "discovery", "-l"], capture_output=True, text=True, timeout=30, check=False)
    assert r.returncode == 0 and "2 GPUs found" in r.stdout, r.stdout + r.stderr
    node = subprocess.run(["docker", "exec", "workbench-gpu001-1", "sh", "-c", "command -v dcgmi nvidia-smi"], capture_output=True, text=True, timeout=30, check=False)
    assert node.returncode != 0  # the nodes carry no NVIDIA tools (the page and docs say so)
