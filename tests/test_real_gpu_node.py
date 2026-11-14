"""The Slurm node on the real card (issue 428, slice 2): a mode of the real-gpu part, its own partition, no BMC or BCM entry yet (issue 431)."""
from pathlib import Path

import pytest
import yaml

from benchctl import addressing, compose, nodes, real_gpu, slurmconf, workbench, workload

ROOT = Path(__file__).resolve().parent.parent
BASE = yaml.safe_load((ROOT / "sim.yaml").read_text())
CARD = ["GPU 0: NVIDIA GeForce RTX 3050 Laptop GPU"]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(real_gpu, "cards", lambda *a, **k: CARD)
    spec = workbench.Spec(cpu_nodes=1, gpu_nodes=1, gpus_per_node=2, parts=["real-gpu"])
    return workbench.effective(BASE, spec)


def test_the_part_adds_the_node_with_the_cards_model_as_its_gres_type(on):
    sim, _ = on
    assert sim["real_gpu"] == {"enabled": True, "node": "rgpu001", "model": "NVIDIA GeForce RTX 3050 Laptop GPU", "type": "geforce-rtx-3050-laptop-gpu"}
    assert ("rgpu001", "real-gpu") in nodes.node_names(sim)
    assert real_gpu.gres_type("NVIDIA A100-SXM4-40GB") == "a100-sxm4-40gb" and real_gpu.gres_type("") == "real"


def test_without_the_part_there_is_no_node():
    sim, _ = workbench.effective(BASE, workbench.Spec(cpu_nodes=1, gpu_nodes=1, gpus_per_node=2, parts=[]))
    assert "real_gpu" not in sim and not [n for n, k in nodes.node_names(sim) if k == "real-gpu"]


def test_the_mocks_do_not_list_it_yet(on):
    sim, _ = on
    assert "rgpu001" not in [n for n, _ in nodes.hardware_nodes(sim)]  # BMC, BCM and the exporter farm: issue 431
    assert "rgpu001" not in addressing.plan(sim)["bmc"]


def test_it_has_a_fixed_address(on):
    sim, _ = on
    assert addressing.plan(sim)["nodes"]["rgpu001"] == addressing.REAL_GPU_IP
    assert addressing.address_of(addressing.plan(sim), "rgpu001") == addressing.REAL_GPU_IP


def test_slurm_gets_its_own_node_line_and_partition_and_the_simulated_gpu_nodes_are_untouched(on):
    sim, _ = on
    conf = slurmconf.slurm_conf(sim)
    assert "NodeName=rgpu001 CPUs=" in conf and "Gres=gpu:geforce-rtx-3050-laptop-gpu:1" in conf
    assert "PartitionName=real-gpu Nodes=rgpu001 State=UP" in conf and "PartitionName=gpu Nodes=gpu001" in conf
    assert "rgpu001" not in conf.split("PartitionName=gpu ")[1].split("\n")[0]  # a simulated job cannot land on the card
    assert "Default=YES" not in next(ln for ln in conf.splitlines() if "real-gpu" in ln)
    assert "NodeName=gpu001 Name=gpu Type=h200 File=/dev/workbench-gpu[0-1]" in slurmconf.gres_conf(sim)
    assert "NodeName=rgpu001 Name=gpu Type=geforce-rtx-3050-laptop-gpu File=/dev/nvidia0" in slurmconf.gres_conf(sim)


def test_gres_conf_works_with_no_simulated_gpu_nodes(monkeypatch):
    monkeypatch.setattr(real_gpu, "cards", lambda *a, **k: CARD)
    sim, _ = workbench.effective(BASE, workbench.Spec(cpu_nodes=1, gpu_nodes=0, gpus_per_node=1, parts=["real-gpu"]))
    assert slurmconf.gres_conf(sim).strip().splitlines()[-1].startswith("NodeName=rgpu001 Name=gpu")


def test_the_node_container_gets_the_card_as_a_device_and_no_fake_gpus(on):
    sim, prof = on
    svc = compose.generate(sim, {**prof, "compute_nodes": 1})["services"]["rgpu001"]
    assert svc["devices"] == [real_gpu.DEVICE] and svc["hostname"] == "rgpu001"
    assert "WORKBENCH_GPUS" not in svc["environment"] and svc["environment"]["WORKBENCH_IB"] == "0"
    assert not {"privileged", "security_opt"} & set(svc) and svc["labels"]["workbench.node"] == "real-gpu"
    assert svc["cap_add"] == ["SYS_PTRACE"]  # the one capability every Slurm node has (issue 674)
    assert "devices" not in compose.generate(sim, {**prof, "compute_nodes": 1})["services"]["gpu001"]


def test_the_exporter_is_the_nodes_exporter(on):
    sim, prof = on
    assert compose.generate(sim, {**prof, "compute_nodes": 1})["services"][real_gpu.SERVICE]["environment"]["DCGM_HOSTNAME"] == "rgpu001"


def test_a_job_for_the_card_goes_to_its_partition_with_one_gpu(on):
    sim, _ = on
    cmd = workload.command(sim, "rgpu001", gpus=1)[-1]
    assert "--partition=real-gpu" in cmd and "--nodelist=rgpu001" in cmd and "--gres=gpu:1" in cmd
    with pytest.raises(workload.WorkloadError, match="1 GPU"):
        workload.command(sim, "rgpu001", gpus=2)


def test_the_page_reads_the_real_nodes_state_from_the_cards_exporter(on, monkeypatch):
    from webui import cluster

    sim, _ = on
    metrics = ('DCGM_FI_DEV_GPU_UTIL{gpu="0",Hostname="rgpu001"} 91\nDCGM_FI_DEV_GPU_TEMP{gpu="0",Hostname="rgpu001"} 55\n'
               'DCGM_FI_DEV_XID_ERRORS{gpu="0",Hostname="rgpu001"} 0\n')

    def fetch(url, body=None, timeout=2.0):
        if url.endswith(":9403/metrics"):
            return metrics
        if url.endswith("/json"):
            return {"result": []}
        raise OSError("down")  # every other service

    monkeypatch.setattr(cluster, "saved_has_part", lambda part: part == "real-gpu")
    row = next(n for n in cluster.Cluster(sim, fetch=fetch).snapshot()["nodes"] if n["name"] == "rgpu001")
    assert row["kind"] == "real-gpu" and row["gpus"] == 1 and row["gpus_busy"] == 1 and row["temp"] == 55.0
    assert (row["state"], row["label"]) == ("ok", "Healthy")


def test_the_node_health_check_reads_the_real_exporter_not_the_farm(on):
    sim, prof = on
    services = compose.generate(sim, {**prof, "compute_nodes": 1})["services"]
    assert services["rgpu001"]["environment"]["WORKBENCH_NHC_URL"] == f"http://{real_gpu.SERVICE}:9400/metrics"
    assert "WORKBENCH_NHC_URL" not in services["gpu001"]["environment"]  # a simulated node stays on the farm (issue 437)
