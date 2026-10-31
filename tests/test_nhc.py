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


def run(tmp_path, text, expected=2, show="", mode=None, ib=0):
    mf, log, stub = tmp_path / "m.txt", tmp_path / "calls.txt", tmp_path / "scontrol"
    mf.write_text(text)
    log.unlink(missing_ok=True)  # a test may run it several times in one directory
    stub.write_text(f'#!/bin/sh\nif [ "$1" = show ]; then printf %s "{show}"; else echo "$@" >> {log}; fi\n')
    stub.chmod(0o755)
    env = {**os.environ, "NHC_METRICS_FILE": str(mf), "NHC_SCONTROL": str(stub), "NHC_NODE": "gpu001",
           "NHC_EXPECTED_GPUS": str(expected), "NHC_EXPECTED_IB": str(ib),
           "NHC_STATE_DIR": str(tmp_path)}
    r = subprocess.run(["bash", str(NHC), *([mode] if mode else [])], check=False, env=env, capture_output=True, text=True, timeout=10)
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
    assert "Prolog=/usr/local/sbin/nhc-prolog" in conf and "Epilog=/usr/local/sbin/nhc-epilog" in conf
    assert "COPY --chmod=755 nhc nhc-prolog nhc-epilog /usr/local/sbin/" in (NHC.parent / "Dockerfile").read_text()
    assert os.access(NHC, os.X_OK)
    for wrapper, mode in (("nhc-prolog", "prolog"), ("nhc-epilog", "epilog")):
        assert os.access(NHC.parent / wrapper, os.X_OK) and f"nhc {mode}" in (NHC.parent / wrapper).read_text()


def ib_metrics(states, node="gpu001", carrier=None):
    lines = [f'node_infiniband_state_id{{node="{node}",Hostname="{node}",device="mlx5_{i}",port="1"}} {s}' for i, s in enumerate(states)]
    if carrier is not None:
        lines.append(f'node_network_carrier_changes_total{{node="{node}",device="eth0"}} {carrier}')
    return "\n".join(lines) + "\n"


def test_a_down_infiniband_port_drains_the_node_and_names_the_port(tmp_path):
    r, calls = run(tmp_path, ib_metrics([4, 1, 4, 4]), expected=0, ib=4)
    assert r.returncode == 1 and calls == ["update nodename=gpu001 state=drain reason=NHC: IB port down: mlx5_1"]
    r, calls = run(tmp_path, ib_metrics([4, 4, 4, 4]), expected=0, ib=4)
    assert r.returncode == 0 and calls == []


def test_a_missing_infiniband_port_is_counted_against_what_the_node_should_have(tmp_path):
    _, calls = run(tmp_path, ib_metrics([4, 4]), expected=0, ib=4)
    assert "IB ports 2 of 4" in calls[0]


def test_a_nic_whose_link_keeps_changing_is_drained_but_a_steady_counter_is_not(tmp_path):
    assert run(tmp_path, ib_metrics([4], carrier=10), expected=0, ib=1)[1] == []   # the first look only remembers the counter
    assert run(tmp_path, ib_metrics([4], carrier=10), expected=0, ib=1)[1] == []
    r, calls = run(tmp_path, ib_metrics([4], carrier=14), expected=0, ib=1)
    assert r.returncode == 1 and "NIC eth0 flapping (4 link changes)" in calls[0]


def test_the_prolog_fails_an_unhealthy_node_so_the_job_is_requeued_and_never_resumes_it(tmp_path):
    r, calls = run(tmp_path, metrics(xid=79), mode="prolog")
    assert r.returncode == 1 and "reason=NHC: GPU XID 79" in calls[0]
    r, calls = run(tmp_path, metrics(), mode="prolog", show="Reason=NHC: GPU XID 79")
    assert r.returncode == 0 and calls == []  # a healthy check at a job start does not heal the node


def test_the_epilog_drains_but_always_succeeds(tmp_path):
    r, calls = run(tmp_path, metrics(xid=79), mode="epilog")
    assert r.returncode == 0 and "state=drain" in calls[0]


def test_a_node_without_ib_or_gpu_expectations_is_not_checked(tmp_path):
    assert run(tmp_path, ib_metrics([1]), expected=0, ib=0)[1] == []
