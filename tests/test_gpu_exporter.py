import importlib.util
import json
import random
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exporter = load("components/gpu-exporter-farm/exporter.py", "exporter")
gen = load("observability/gen_targets.py", "gen_targets")


def test_nodes_from_sim_yaml():
    nodes = exporter.load_nodes(ROOT / "sim.yaml")
    assert nodes["cn001"] == 0 and nodes["gpu001"] == 8
    assert len(nodes) == 6


def test_render_has_gpu_and_fs_metrics():
    s = exporter.State(exporter.load_nodes(ROOT / "sim.yaml"), random.Random(1))
    text = s.render()
    assert text.count("DCGM_FI_DEV_GPU_UTIL{") == 16
    assert 'node_filesystem_free_bytes{node="cn001",mountpoint="/mnt/lustre"}' in text


def test_inject_xid_and_clear():
    s = exporter.State({"gpu001": 1}, random.Random(1))
    s.inject({"node": "gpu001", "xid": 79, "temp": 92})
    t = s.render()
    assert 'DCGM_FI_DEV_XID_ERRORS{node="gpu001",gpu="0"} 79' in t
    assert 'DCGM_FI_DEV_GPU_TEMP{node="gpu001",gpu="0"} 92.0' in t
    s.inject({"node": "gpu001", "clear": True})
    assert 'XID_ERRORS{node="gpu001",gpu="0"} 0' in s.render()


def test_inject_unknown_node():
    s = exporter.State({"gpu001": 1})
    try:
        s.inject({"node": "nope", "xid": 1})
    except KeyError:
        return
    raise AssertionError


def test_gen_targets_names_agree():
    t = gen.targets(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert t[0]["labels"]["nodes"] == "cn001,cn002,cn003,cn004,gpu001,gpu002"


def test_dashboard_json_valid():
    d = json.loads((ROOT / "observability/dashboards/hpc-overview.json").read_text())
    assert d["uid"] == "hpc-overview" and len(d["panels"]) == 6
