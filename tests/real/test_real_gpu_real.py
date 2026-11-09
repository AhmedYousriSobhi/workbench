"""The host's own NVIDIA card in a container (issue 428): real series, the real model name, dcgmi and nvidia-smi inside it.
Skips on a machine with no NVIDIA card. Not run by the agent: `./scripts/verify.sh --unit real_gpu --clean`."""
import os
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest
from helpers import wait_for

from benchctl import real_gpu, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(bool(real_gpu.problems()), reason="no NVIDIA card with its driver and CDI spec on this machine")
CONTAINER = "workbench-real-gpu-1"


def scrape() -> str:
    """The metrics, or "" while the exporter is not answering yet (the port is published before it listens: a reset or a refusal)."""
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{real_gpu.PORT}/metrics", timeout=5).read().decode()
    except OSError:
        return ""


def run(*cmd: str, timeout: int = 1200) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


@pytest.fixture(scope="module")
def built():
    os.chdir(ROOT)
    out = run(str(ROOT / "scripts/benchctl"), "up", "--cpu-nodes", "1", "--gpu-nodes", "1", "--with", "real-gpu", "--app-mb", "0")  # no app runs here: leave its 800 MB reserve out of the plan
    assert out.returncode == 0, out.stdout + out.stderr
    assert wait_for(lambda: "DCGM_FI_DEV_GPU_UTIL" in scrape(), timeout=120), "the real exporter never answered"
    yield
    stack.down(say=lambda m: None)
    workbench.forget()


def test_the_series_are_the_real_cards(built):
    text = scrape()
    model = real_gpu.cards()[0].split(": ", 1)[1]
    assert f'modelName="{model}"' in text and f'Hostname="{real_gpu.HOSTNAME}"' in text
    used, free = (float(re.search(rf'^DCGM_FI_DEV_{m}{{[^}}]*}} (\S+)', text, re.MULTILINE)[1]) for m in ("FB_USED", "FB_FREE"))
    assert used + free > 1000  # the card's framebuffer in MiB
    assert 0 < float(re.search(r'^DCGM_FI_DEV_GPU_TEMP{[^}]*} (\S+)', text, re.MULTILINE)[1]) < 120


def test_the_container_is_unprivileged_and_has_the_tools(built):
    info = run("docker", "inspect", CONTAINER, "--format", "{{.HostConfig.Privileged}} {{len .HostConfig.CapAdd}}")
    assert info.stdout.split() == ["false", "0"], info.stdout
    assert "GPU 0" in run("docker", "exec", CONTAINER, "dcgmi", "discovery", "-l").stdout or "1 GPU found" in run("docker", "exec", CONTAINER, "dcgmi", "discovery", "-l").stdout
    assert run("docker", "exec", CONTAINER, "nvidia-smi", "-L").returncode == 0
    assert run("docker", "exec", "workbench-gpu001-1", "sh", "-c", "command -v nvidia-smi").returncode != 0  # the simulated nodes still have none


def slurm(*cmd: str) -> subprocess.CompletedProcess:
    return run("docker", "exec", "-u", "alice", "workbench-login-1", *cmd, timeout=180)


def test_a_job_in_the_real_partition_runs_on_the_card_and_a_simulated_job_cannot(built):
    assert wait_for(lambda: "idle" in slurm("sinfo", "-h", "-p", real_gpu.PARTITION, "-o", "%T").stdout, timeout=120), slurm("sinfo").stdout
    job = slurm("sbatch", "--wait", "-p", real_gpu.PARTITION, "--gres=gpu:1", "-o", "/tmp/real-gpu.out", "--wrap=nvidia-smi -L")
    assert job.returncode == 0, job.stdout + job.stderr
    assert "GPU 0: " in run("docker", "exec", f"workbench-{real_gpu.NODE}-1", "cat", "/tmp/real-gpu.out").stdout  # it ran on the node with the card
    other = slurm("sbatch", "-p", "gpu", f"--nodelist={real_gpu.NODE}", "--wrap=true")
    assert other.returncode != 0  # the node is in no other partition
    assert "real" in slurm("sinfo", "-h", "-N", "-o", "%N %G").stdout
