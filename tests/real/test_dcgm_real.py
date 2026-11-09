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
    """The metrics, or "" while the exporter is not answering yet (the port is published before it listens: a reset or a refusal)."""
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
    except OSError:
        return ""


def value(metric: str, gpu: int) -> float | None:
    m = re.search(rf'^{metric}{{[^}}]*gpu="{gpu}"[^}}]*}} (\S+)', scrape(), re.MULTILINE)
    return float(m[1]) if m else None


def yaml_sim() -> dict:
    import yaml
    return yaml.safe_load((ROOT / "sim.yaml").read_text())


def slurm(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-u", "alice", "workbench-login-1", *cmd], capture_output=True, text=True, timeout=30, check=False)


@pytest.fixture(scope="module")
def built():
    os.chdir(ROOT)
    out = benchctl("up", "--cpu-nodes", "1", "--gpu-nodes", "2", "--gpus-per-node", "2", "--with", "dcgm", "--dcgm-nodes", "2", "--with", "scenarios")
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


def inject(**body) -> None:
    req = urllib.request.Request("http://127.0.0.1:9400/sim/inject", data=json.dumps({"node": "gpu001", **body}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    assert urllib.request.urlopen(req, timeout=5).status == 200


def test_an_injected_xid_shows_on_one_gpu_and_a_clear_restarts_the_exporter(built):
    inject(xid=63)
    assert wait_for(lambda: value("DCGM_FI_DEV_XID_ERRORS", 0) == 63, timeout=60), scrape()[:400]
    assert value("DCGM_FI_DEV_XID_ERRORS", 1) in (None, 0)
    # DCGM keeps the last XID until its host engine restarts, so the scenario engine restarts the exporter on a clear (issue 435)
    from benchctl import scenario
    out = scenario.execute(scenario.Step(0, "gpu-exporter", {"nodes": ["gpu001"], "clear": True}))
    assert out.status == "ok", out
    assert wait_for(lambda: "DCGM_FI_DEV_GPU_UTIL" in scrape() and value("DCGM_FI_DEV_XID_ERRORS", 0) in (None, 0), timeout=120), scrape()[:400]


def test_a_dead_node_stops_its_real_exporter_and_it_comes_back_with_the_node(built):
    from benchctl import scenario
    assert scenario.execute(scenario.Step(0, "gpu-exporter", {"nodes": ["gpu002"], "down": True})).status == "ok"
    assert wait_for(lambda: scrape(dcgm.EXTRA_PORT) == "", timeout=30), "the stopped node's exporter still answers"
    assert "DCGM_FI_DEV_GPU_UTIL" in scrape()  # the other node is unaffected
    assert scenario.execute(scenario.Step(0, "gpu-exporter", {"nodes": ["gpu002"], "down": False})).status == "ok"
    assert wait_for(lambda: "DCGM_FI_DEV_GPU_UTIL" in scrape(dcgm.EXTRA_PORT), timeout=120)


def test_two_nodes_two_exporters_with_their_own_hostname_and_gpu_uuids(built):
    first, second = scrape(), scrape(dcgm.EXTRA_PORT)
    assert 'Hostname="gpu001"' in first and 'Hostname="gpu002"' in second
    uuids = lambda text: set(re.findall(r'UUID="(GPU-[^"]+)"', text))
    assert uuids(first) and uuids(second) and not uuids(first) & uuids(second)


def test_h200_shows_its_own_model_name(built):
    sim = workbench.effective(yaml_sim(), workbench.current(yaml_sim()))[0]
    if str(sim["gpu"].get("type", "")).lower() != "h200":
        pytest.skip("sim.yaml's GPU type is not h200")
    assert 'modelName="NVIDIA H200"' in scrape()


def test_dcgmi_works_in_the_exporter_container_and_only_there(built):
    r = subprocess.run(["docker", "exec", "workbench-dcgm-exporter-1", "dcgmi", "discovery", "-l"], capture_output=True, text=True, timeout=30, check=False)
    assert r.returncode == 0 and "2 GPUs found" in r.stdout, r.stdout + r.stderr
    node = subprocess.run(["docker", "exec", "workbench-gpu001-1", "sh", "-c", "command -v dcgmi nvidia-smi"], capture_output=True, text=True, timeout=30, check=False)
    assert node.returncode != 0  # the nodes carry no NVIDIA tools (the page and docs say so)
