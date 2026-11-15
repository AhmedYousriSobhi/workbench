"""MIG-style typed GRES (issue 546, ADR 0075): off by default, one typed unit per slice on the listed nodes."""
import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

from benchctl import compose, hwinventory, mig, slurmconf

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def with_mig(**over):
    sim = copy.deepcopy(SIM)
    sim["gpu"]["mig"] = {"nodes": ["gpu002"], "profile": "1g", "slices": 7, **over}
    return sim


def test_off_by_default_nothing_changes():
    assert mig.config(SIM) is None and mig.gres_type(SIM) is None and not mig.is_mig(SIM, "gpu001")
    conf = slurmconf.slurm_conf(SIM, 4)
    assert "NodeName=gpu[001-002] CPUs=64 RealMemory=16384 Gres=gpu:h200:8" in conf
    assert "gpu_mig" not in str(hwinventory.build(SIM)["nodes"])


def test_the_listed_node_gets_typed_gres_and_the_other_keeps_its_plain_gpus():
    sim = with_mig()
    assert mig.gres_type(sim) == "h200_1g" and mig.units(sim) == 56  # 8 GPUs x 7 slices
    conf = slurmconf.slurm_conf(sim, 4)
    assert "NodeName=gpu002 CPUs=64 RealMemory=16384 Gres=gpu:h200_1g:56 State=UNKNOWN" in conf
    assert "NodeName=gpu001 CPUs=64 RealMemory=16384 Gres=gpu:h200:8 State=UNKNOWN" in conf
    assert "PartitionName=gpu Nodes=gpu[001-002]" in conf  # the partition still holds both
    gres = slurmconf.gres_conf(sim)
    assert "NodeName=gpu002 Name=gpu Type=h200_1g File=/dev/workbench-gpu[0-55]" in gres
    assert "NodeName=gpu001 Name=gpu Type=h200 File=/dev/workbench-gpu[0-7]" in gres


def test_every_gpu_node_can_be_listed_and_slices_default_to_seven():
    sim = with_mig(nodes=["gpu001", "gpu002"], profile="3g", slices=2)
    assert "NodeName=gpu[001-002] CPUs=64 RealMemory=16384 Gres=gpu:h200_3g:16" in slurmconf.slurm_conf(sim, 4)
    sim = with_mig()
    del sim["gpu"]["mig"]["slices"]
    assert mig.config(sim)["slices"] == 7


def test_the_inventory_carries_the_typed_shape_for_the_mig_node_only():
    nodes = {n["name"]: n for n in hwinventory.build(with_mig())["nodes"]}
    assert nodes["gpu002"]["gpu_mig"] == {"type": "h200_1g", "profile": "1g", "slices_per_gpu": 7, "gres_count": 56}
    assert nodes["gpu002"]["gpus"] == 8 and "gpu_mig" not in nodes["gpu001"] and "gpu_mig" not in nodes["cn001"]


def test_compose_gives_the_node_one_device_file_per_slice_and_keeps_nhc_on_physical_gpus():
    prof = compose.load_profile("min")
    svc = compose.generate(with_mig(), prof)["services"]
    assert svc["gpu002"]["environment"]["WORKBENCH_GPUS"] == "8"  # nhc counts the exporter's physical GPU series
    assert svc["gpu002"]["environment"]["WORKBENCH_GRES_DEVICES"] == "56"
    assert "WORKBENCH_GRES_DEVICES" not in svc["gpu001"]["environment"]
    assert svc["slurmctld"]["environment"]["WORKBENCH_MIG_TYPE"] == "h200_1g" and svc["slurmctld"]["environment"]["WORKBENCH_MIG_SLICES"] == "7"


@pytest.mark.parametrize("raw,message", [
    ({"nodes": []}, "must be a list"),
    ({"nodes": "gpu002"}, "must be a list"),
    ({"nodes": ["cn001"]}, "cn001, which is not a GPU node"),
    ({"nodes": ["gpu009"]}, "gpu009"),
    ({"nodes": ["gpu002"], "profile": "1G.18GB"}, "lowercase letters and digits"),
    ({"nodes": ["gpu002"], "slices": 8}, "from 1 to 7"),
    ({"nodes": ["gpu002"], "slices": 0}, "from 1 to 7"),
    ({"nodes": ["gpu002"], "slices": True}, "from 1 to 7"),
])
def test_a_wrong_section_is_refused_with_one_message(raw, message):
    sim = copy.deepcopy(SIM)
    sim["gpu"]["mig"] = raw
    with pytest.raises(mig.MigError, match=message):
        mig.config(sim)


def test_the_allocation_feed_maps_a_slice_to_its_physical_gpu():
    spec = importlib.util.spec_from_file_location("alloc_feed_mig", ROOT / "images/slurm-ctl/alloc_feed.py")
    feed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(feed)
    line = "JobId=7 JobState=RUNNING Nodes=gpu002 CPU_IDs=0-3 Mem=1024 GRES=gpu:h200_1g:9(IDX:6-14)"
    assert feed.parse_jobs(line, "h200_1g", 7) == {"gpu002": [0, 1, 2]}  # slices 6..14 live on GPUs 0, 1 and 2
    plain = "JobId=8 JobState=RUNNING Nodes=gpu001 CPU_IDs=0-3 Mem=1024 GRES=gpu:h200:2(IDX:0-1)"
    assert feed.parse_jobs(plain, "h200_1g", 7) == {"gpu001": [0, 1]}  # an untyped or other-typed GPU is untouched
    assert feed.parse_jobs(plain) == {"gpu001": [0, 1]}
