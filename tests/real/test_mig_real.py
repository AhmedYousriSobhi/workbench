"""MIG-style typed GRES on the live stack (issue 546, ADR 0075). Own cold start with `gpu.mig` for gpu002 (ISOLATED_MODULES). Run:
`./scripts/verify.sh --unit mig`. Asserts on what slurmctld says (`scontrol show node`, a job's allocation), on the exporter's busy
GPUs and on the node health check, never on config text."""
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import compose, stack

ROOT = Path(__file__).resolve().parents[2]
CTL, LOGIN, GPU2 = "workbench-slurmctld-1", "workbench-login-1", "workbench-gpu002-1"


def run(c: str, *cmd: str, user: str | None = None) -> str:
    r = subprocess.run(["docker", "exec", *(["-u", user] if user else []), c, *cmd], capture_output=True, text=True, timeout=60, check=False)
    return r.stdout + r.stderr


def node(name: str) -> str:
    return run(CTL, "scontrol", "show", "node", name)


def busy_gpus(name: str) -> list[int]:
    text = urllib.request.urlopen("http://127.0.0.1:9400/metrics", timeout=5).read().decode()
    return sorted(int(m[1]) for m in re.finditer(rf'DCGM_FI_DEV_GPU_UTIL{{node="{name}",gpu="(\d+)"[^}}]*}} (\S+)', text) if float(m[2]) >= 70)


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    sim["gpu"]["mig"] = {"nodes": ["gpu002"], "profile": "1g", "slices": 7}
    prof = compose.load_profile("min")
    said: list[str] = []
    assert stack.up(sim, "min", say=said.append, available=64 * 1024**3, prof=prof), " | ".join(said[-4:])
    assert wait_for(lambda: "Gres=gpu:h200_1g:56" in node("gpu002"), timeout=150), node("gpu002")
    assert wait_for(lambda: "State=IDLE" in node("gpu002") and "State=IDLE" in node("gpu001"), timeout=120), (node("gpu001"), node("gpu002"))
    yield
    run(LOGIN, "scancel", "-u", "alice")
    stack.down(say=lambda m: None)


def test_the_mig_node_advertises_typed_gres_and_the_other_keeps_plain_gpus(cluster):
    assert "Gres=gpu:h200_1g:56" in node("gpu002") and "Gres=gpu:h200:8" in node("gpu001")
    assert run(GPU2, "sh", "-c", "ls /dev/workbench-gpu* | wc -l").strip() == "56"  # one stand-in file per slice


def test_a_job_asks_for_slices_by_type_and_is_placed_on_the_mig_node(cluster):
    out = run(LOGIN, "sbatch", "--parsable", "-D", "/tmp", "-p", "gpu", "--gres=gpu:h200_1g:9", "--wrap=sleep 300", user="alice")
    job = out.strip().split(";")[0]
    assert job.isdigit(), out
    assert wait_for(lambda: "RUNNING" in run(CTL, "squeue", "-h", "-j", job, "-o", "%T"), timeout=60), run(CTL, "squeue", "-j", job)
    assert "gpu002" in run(CTL, "squeue", "-h", "-j", job, "-o", "%N")  # only gpu002 has that type
    assert "gpu:h200_1g:9" in node("gpu002")  # the allocation is in GresUsed, by type
    # 9 slices (indexes 0-8) live on physical GPUs 0 and 1 (7 slices each): the exporter shows exactly those busy
    assert wait_for(lambda: busy_gpus("gpu002") == [0, 1], timeout=60), busy_gpus("gpu002")
    assert busy_gpus("gpu001") == []
    run(LOGIN, "scancel", job, user="alice")


def test_an_untyped_request_still_works_and_the_health_check_stays_quiet(cluster):
    out = run(LOGIN, "sbatch", "--parsable", "-D", "/tmp", "-p", "gpu", "--nodelist=gpu001", "--gres=gpu:2", "--wrap=sleep 60", user="alice")
    assert out.strip().split(";")[0].isdigit(), out
    assert wait_for(lambda: "RUNNING" in run(CTL, "squeue", "-h", "-u", "alice", "-o", "%T"), timeout=60)
    run(LOGIN, "scancel", "-u", "alice")
    assert "DRAIN" not in node("gpu002") and "DRAIN" not in node("gpu001")  # nhc expects the physical 8, which the exporter shows
    inv = json.loads((ROOT / "compose/generated/inventory.json").read_text())
    assert {n["name"]: n.get("gpu_mig", {}).get("gres_count") for n in inv["nodes"] if n["kind"] == "gpu"} == {"gpu001": None, "gpu002": 56}
