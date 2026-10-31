"""images/node-base/nhc: drains a GPU node on a missing GPU or an XID, resumes only its own drain (issue 141)."""
import os
import subprocess
from pathlib import Path

from benchctl import slurmconf

NHC = Path(__file__).resolve().parent.parent / "images" / "node-base" / "nhc"


def metrics(node="gpu001", gpus=2, xid=None):
    lines = [f'DCGM_FI_DEV_GPU_TEMP{{node="{node}",gpu="{g}",Hostname="{node}"}} 40.0' for g in range(gpus)]
    lines += [f'DCGM_FI_DEV_XID_ERRORS{{node="{node}",gpu="{g}",Hostname="{node}"}} {xid if (xid and g == 0) else 0}' for g in range(gpus)]
    lines += ['DCGM_FI_DEV_GPU_TEMP{node="gpu002",gpu="0",Hostname="gpu002"} 40.0']  # another node's series are not ours
    return "\n".join(lines) + "\n"


def run(tmp_path, text, expected=2, show=""):
    mf, log, stub = tmp_path / "m.txt", tmp_path / "calls.txt", tmp_path / "scontrol"
    mf.write_text(text)
    stub.write_text(f'#!/bin/sh\nif [ "$1" = show ]; then printf %s "{show}"; else echo "$@" >> {log}; fi\n')
    stub.chmod(0o755)
    env = {**os.environ, "NHC_METRICS_FILE": str(mf), "NHC_SCONTROL": str(stub), "NHC_NODE": "gpu001",
           "NHC_EXPECTED_GPUS": str(expected)}
    r = subprocess.run(["bash", str(NHC)], check=False, env=env, capture_output=True, text=True, timeout=10)
    return r, (log.read_text().splitlines() if log.exists() else [])


def test_healthy_node_is_left_alone(tmp_path):
    r, calls = run(tmp_path, metrics())
    assert r.returncode == 0 and calls == []


def test_an_xid_drains_the_node_with_the_code_in_the_reason(tmp_path):
    r, calls = run(tmp_path, metrics(xid=79))
    assert r.returncode == 1 and calls == ["update nodename=gpu001 state=drain reason=NHC: GPU XID 79"]


def test_a_missing_gpu_drains_the_node(tmp_path):
    r, calls = run(tmp_path, metrics(gpus=1))
    assert r.returncode == 1 and "reason=NHC: GPU count 1 of 2" in calls[0]


def test_both_problems_are_named_in_one_reason(tmp_path):
    _, calls = run(tmp_path, metrics(gpus=1, xid=79))
    assert "GPU count 1 of 2; GPU XID 79" in calls[0]


def test_a_node_that_publishes_nothing_is_off_not_unhealthy(tmp_path):
    r, calls = run(tmp_path, "DCGM_FI_DEV_GPU_TEMP{node=\"gpu002\",Hostname=\"gpu002\"} 1\n")
    assert r.returncode == 0 and calls == []


def test_nodes_without_gpus_are_skipped(tmp_path):
    r, calls = run(tmp_path, metrics(xid=79), expected=0)
    assert r.returncode == 0 and calls == []


def test_it_resumes_only_a_drain_it_made(tmp_path):
    _, calls = run(tmp_path, metrics(), show="State=IDLE+DRAIN Reason=NHC: GPU XID 79 [root@x]")
    assert calls == ["update nodename=gpu001 state=resume"]
    (tmp_path / "other").mkdir()
    _, calls = run(tmp_path / "other", metrics(), show="State=IDLE+DRAIN Reason=maintenance [root@x]")
    assert calls == []


def test_slurm_runs_it_on_every_node_and_the_image_ships_it():
    conf = slurmconf.slurm_conf({"compute": {"prefix": "cn", "count": 1, "cpus": 2, "memory_mb": 100}, "gpu": {}}, 1)
    assert "HealthCheckProgram=/usr/local/sbin/nhc" in conf and "HealthCheckNodeState=ANY" in conf
    assert "COPY --chmod=755 nhc /usr/local/sbin/nhc" in (NHC.parent / "Dockerfile").read_text()
    assert os.access(NHC, os.X_OK)
