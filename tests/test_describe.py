"""benchctl describe node: one gatherer for the CLI and the web page (issue 93)."""
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from simctl import cli, describe

SIM = yaml.safe_load((Path(__file__).resolve().parent.parent / "sim.yaml").read_text())
METRICS = """\
DCGM_FI_DEV_GPU_UTIL{node="gpu001",gpu="0",Hostname="gpu001"} 90
DCGM_FI_DEV_GPU_UTIL{node="gpu001",gpu="1",Hostname="gpu001"} 88
DCGM_FI_DEV_GPU_UTIL{node="gpu001",gpu="2",Hostname="gpu001"} 1
DCGM_FI_DEV_GPU_UTIL{node="gpu002",gpu="0",Hostname="gpu002"} 99
DCGM_FI_DEV_XID_ERRORS{node="gpu001",gpu="2",Hostname="gpu001"} 79"""
SCONTROL = ("NodeName=gpu001 Arch=x86_64 CPUTot=8 CPUAlloc=2 RealMemory=4096 Gres=gpu:h200:8 "
            "State=MIXED Partitions=gpu Reason=(null)")
INSPECT = json.dumps([{"State": {"Status": "running"}, "Config": {"Image": "hpcsim/node:dev", "Labels": {"hpcsim.class": "P4"}},
                       "HostConfig": {"Memory": 60 * 1024 * 1024}}])


def fetch(url):
    if url.endswith("/Systems/1"):
        return {"PowerState": "On"}
    if url.endswith("/Thermal"):
        return {"Temperatures": [{"Name": "CPU", "ReadingCelsius": 61}],
                "Fans": [{"Status": {"Health": "OK"}}, {"Status": {"Health": "Critical"}}]}
    if url.endswith("/metrics"):
        return METRICS
    raise OSError("down")


def runner(cmd):
    out = {"inspect": INSPECT, "scontrol": SCONTROL, "squeue": "42|alice|gpu|RUNNING\n"}[
        next(k for k in ("inspect", "scontrol", "squeue") if k in cmd)]
    return subprocess.CompletedProcess(cmd, 0, out, "")


def test_gpu_node_gathers_every_source():
    info = describe.gather(SIM, "gpu001", fetch, runner)
    assert info["container"] == {"state": "running", "image": "hpcsim/node:dev", "memory_limit_mb": 60, "class": "P4"}
    assert info["slurm"]["State"] == "MIXED" and info["slurm"]["Partitions"] == "gpu" and "Reason" not in info["slurm"]
    assert info["jobs"] == [{"id": "42", "user": "alice", "partition": "gpu", "state": "RUNNING"}]
    assert info["gpus"] == {"type": "h200", "count": 8, "busy": [0, 1], "idle": [2], "xid": 79}
    assert info["bmc"]["power"] == "On" and info["bmc"]["fans_failed"] == 1 and info["bmc"]["cpu_temp_c"] == 61
    assert info["notes"] == []


def test_gpu_busy_list_is_per_node():
    assert describe.gather(SIM, "gpu002", fetch)["gpus"]["busy"] == [0]


def test_compute_node_has_no_gpu_block_and_web_mode_has_no_docker_fields():
    info = describe.gather(SIM, "cn001", fetch)  # no runner: what the web page can see
    assert "gpus" not in info and "container" not in info and "slurm" not in info and info["bmc"]["power"] == "On"


def test_a_source_that_is_down_is_one_note_not_a_crash():
    def down(url):
        raise OSError("refused")
    info = describe.gather(SIM, "gpu001", down)
    assert "bmc" not in info and "gpus" not in info
    assert info["notes"] == ["BMC farm did not answer", "GPU exporter did not answer"]


def test_absent_container_says_how_to_start_it():
    def none(cmd):
        return subprocess.CompletedProcess(cmd, 1, "", "no such container")
    info = describe.gather(SIM, "cn001", fetch, none)
    assert info["container"] == {"state": "absent"} and any("benchctl up" in n for n in info["notes"])
    assert "Slurm did not answer for this node" in info["notes"]


def test_unknown_node_lists_the_real_ones():
    with pytest.raises(describe.UnknownNode, match="cn001"):
        describe.gather(SIM, "nope", fetch)


def test_render_reads_like_kubectl_describe():
    text = describe.render(describe.gather(SIM, "gpu001", fetch, runner))
    for want in ("Name:        gpu001", "Container:   running", "memory limit 60 MB", "State:", "42 (alice, gpu, RUNNING)",
                 "8 x h200; busy [0, 1]", "XID 79", "power On, CPU 61 C, fans 2 (1 failed)", "ssh gpu001"):
        assert want in text, want


def test_ports_match_the_web_page():
    from webui import cluster

    assert (describe.BMC, describe.EXPORTER) == (cluster.BMC, cluster.EXPORTER)


def test_cli_describe_prints_text_json_and_refuses_an_unknown_node(monkeypatch):
    monkeypatch.setattr(describe, "http_get", fetch)
    monkeypatch.setattr("simctl.stack._run", runner)
    r = CliRunner().invoke(cli.app, ["describe", "node", "gpu001"])
    assert r.exit_code == 0 and "GPUs:" in r.output
    assert json.loads(CliRunner().invoke(cli.app, ["describe", "node", "gpu001", "-o", "json"]).output)["name"] == "gpu001"
    bad = CliRunner().invoke(cli.app, ["describe", "node", "zzz"])
    assert bad.exit_code == 2 and "unknown node" in bad.output
