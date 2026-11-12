"""One inventory, one shape: Slurm, Redfish, BCM and the GPU exporter agree on every node (issue 462, SPEC section 3)."""
import copy
import importlib.util
import json
import random
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest
import yaml
from invhelp import inv

from benchctl import hwinventory, slurmconf

ROOT = Path(__file__).resolve().parents[1]


def load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bmc = load("components/bmc-farm/bmc.py", "bmc_inv")
bcm = load("components/bcm-mock/bcm.py", "bcm_inv")
exporter = load("components/gpu-exporter-farm/exporter.py", "exporter_inv")
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def expand(spec: str) -> list[str]:
    m = re.fullmatch(r"(\D+)\[(\d+)-(\d+)\]", spec)
    return [f"{m.group(1)}{i:0{len(m.group(2))}d}" for i in range(int(m.group(2)), int(m.group(3)) + 1)] if m else [spec]


def slurm_shapes(sim: dict) -> dict[str, dict]:
    out = {}
    for line in slurmconf.slurm_conf(sim).splitlines():
        m = re.match(r"NodeName=(\S+) CPUs=(\d+) RealMemory=(\d+)(?: Gres=gpu:[^:]+:(\d+))?", line)
        if m:
            for n in expand(m.group(1)):
                out[n] = {"cpus": int(m.group(2)), "memory_mb": int(m.group(3)), "gpus": int(m.group(4) or 0)}
    return out


def redfish_shapes(path: Path) -> dict[str, dict]:
    farm = bmc.Farm(bmc.load_nodes(path))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bmc.make_handler(farm))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        out = {}
        for n in farm.nodes:
            s = json.loads(urlopen(f"http://127.0.0.1:{srv.server_port}/{n}/redfish/v1/Systems/1", timeout=5).read())
            out[n] = {"cpus": s["ProcessorSummary"]["Count"], "memory_mb": s["MemorySummary"]["TotalSystemMemoryGiB"] * 1024, "serial": s["SerialNumber"]}
        return out
    finally:
        srv.shutdown()


def gpu_uuids(path: Path) -> dict[str, list[str]]:
    text = exporter.State(exporter.load_nodes(path), random.Random(1)).render()
    out: dict[str, list[str]] = {}
    for m in re.finditer(r'DCGM_FI_DEV_GPU_UTIL\{node="([^"]+)",gpu="(\d+)".*?UUID="([^"]+)"', text):
        out.setdefault(m.group(1), []).append(m.group(3))
    return out


def with_gpu_shape(**shape) -> dict:
    sim = copy.deepcopy(SIM)
    sim["gpu"] = {k: v for k, v in sim["gpu"].items() if k not in ("cpus", "memory_mb")} | shape
    return sim


@pytest.mark.parametrize("sim", [SIM, with_gpu_shape(), with_gpu_shape(cpus=96, memory_mb=20480)], ids=["sim.yaml", "per-gpu default", "explicit"])
def test_slurm_redfish_bcm_and_dcgm_agree_on_every_node(sim):
    path = inv(sim)
    inventory = json.loads(path.read_text())["nodes"]
    slurm, redfish = slurm_shapes(sim), redfish_shapes(path)
    devices = {n: v for n, v in bcm.load_nodes(path).items()}
    dcgm = exporter.load_nodes(path)
    for node in inventory:
        name, want = node["name"], {"cpus": node["cpus"], "memory_mb": node["memory_mb"]}
        assert {k: slurm[name][k] for k in want} == want and slurm[name]["gpus"] == node["gpus"], name
        assert {k: redfish[name][k] for k in want} == want, name
        assert {k: devices[name][k] for k in want} == want and devices[name]["gpus"] == node["gpus"], name
        assert dcgm[name] == node["gpus"], name
    assert set(slurm) == {n["name"] for n in inventory if n["kind"] in ("compute", "gpu")}


def test_a_gpu_node_has_the_same_shape_everywhere_by_default():
    """The drift that issue 462 fixed: 8 CPUs and 4096 MB in Slurm, 64 and 16384 in Redfish."""
    gpu = next(n for n in json.loads(inv(SIM).read_text())["nodes"] if n["kind"] == "gpu")
    assert (gpu["cpus"], gpu["memory_mb"], gpu["gpus"]) == (64, 16384, 8)
    assert "CPUs=64 RealMemory=16384 Gres=gpu:h200:8" in slurmconf.slurm_conf(SIM)


def test_serials_and_gpu_uuids_in_the_inventory_are_what_the_mocks_publish():
    path = inv(SIM)
    nodes = json.loads(path.read_text())["nodes"]
    redfish, uuids = redfish_shapes(path), gpu_uuids(path)
    for n in nodes:
        assert redfish[n["name"]]["serial"] == n["serial"] == hwinventory.serial(n["name"])
        assert uuids.get(n["name"], []) == n["gpu_uuids"], n["name"]


def test_kubernetes_nodes_are_in_the_inventory_and_bcm_lists_them_only_when_it_manages_them():
    sim = copy.deepcopy(SIM)
    sim["kubernetes"] = {**sim.get("kubernetes", {}), "enabled": True}
    inventory = hwinventory.build(sim)
    k8s = [n for n in inventory["nodes"] if n["kind"].startswith("k8s")]
    assert k8s and all(n["serial"] and len(n["gpu_uuids"]) == n["gpus"] for n in k8s)
    on = bcm.load_nodes(inv(sim))
    assert {n["name"] for n in k8s} <= set(on) and all(n["name"] in dict(exporter.load_nodes(inv(sim))) for n in k8s)
    sim["bcm"] = {"manage_kubernetes": False}
    assert not {n["name"] for n in k8s} & set(bcm.load_nodes(inv(sim)))


def test_no_mock_reads_sim_yaml():
    for rel in ("components/bmc-farm/bmc.py", "components/bcm-mock/bcm.py", "components/gpu-exporter-farm/exporter.py"):
        text = (ROOT / rel).read_text()
        assert "safe_load(sim" not in text and "sim_yaml" not in text and '"sim.yaml"' not in text, rel
