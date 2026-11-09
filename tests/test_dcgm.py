"""The real DCGM exporter part (issue 426): wiring, the profile choice and what the farm tells the mock NVML."""
import importlib.util
from pathlib import Path

import pytest
import yaml

from benchctl import compose, dcgm, workbench

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def farm():
    spec = importlib.util.spec_from_file_location("exporter", ROOT / "components/gpu-exporter-farm/exporter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def services(parts_on: bool):
    prof = {"groups": ["core", "dcgm"] if parts_on else ["core"], "compute_nodes": 1}
    return compose.generate(SIM, prof)["services"]


def test_off_by_default_and_by_name_only():
    assert dcgm.SERVICE not in services(False)
    assert "dcgm" in workbench.BY_NAME_ONLY and "dcgm" in workbench.PART_CATEGORY and "dcgm" in workbench.PART_INFO


def test_wired_with_the_mock_profile_gpu_count_and_a_localhost_port():
    svc = services(True)[dcgm.SERVICE]
    env = svc["environment"]
    assert env["MOCK_NVML_NUM_DEVICES"] == str(SIM["gpu"]["gpus_per_node"])
    assert env["MOCK_NVML_CONFIG"].endswith(f"mock-nvml-config-{dcgm.profile(SIM)}.yaml")
    assert svc["hostname"] == "gpu001" and svc["ports"] == [f"127.0.0.1:{dcgm.PORT}:9400"]
    assert svc["mem_limit"] == f"{dcgm.LIMIT_MB}m" and svc["labels"]["workbench.class"] == "P6"
    assert not {"privileged", "cap_add", "security_opt"} & set(svc)  # stays unprivileged


@pytest.mark.parametrize("gpu_type,want", [("a100", "a100"), ("H200", "h100"), ("l40s", "l40s"), ("", "h100"), ("x9", "h100")])
def test_the_profile_follows_the_gpu_type(gpu_type, want):
    assert dcgm.profile({"gpu": {"type": gpu_type}}) == want


def test_it_needs_a_gpu_node():
    assert dcgm.problems(SIM) == [] and dcgm.node(SIM) == "gpu001"
    assert "GPU node" in dcgm.problems({"gpu": {"nodes": 0, "prefix": "gpu"}})[0]


def test_prepare_makes_a_directory_the_farm_can_write(tmp_path):
    dcgm.prepare(tmp_path)
    assert (tmp_path / "dcgm").stat().st_mode & 0o777 == 0o777 and (tmp_path / "dcgm" / "overrides.yaml").exists()


def state():
    ex = farm()
    return ex, ex.State({"cn001": 0, "gpu001": 2, "gpu002": 2}, gpu_type="a100")


def test_a_held_gpu_works_hard_and_memory_stays_coherent():
    ex, st = state()
    st.set_alloc({"gpu001": [1]})
    dev = st.dcgm_overrides()["devices"]
    assert dev[1]["utilization"]["gpu"] > 70 and dev[0]["utilization"]["gpu"] == 0
    for d in dev.values():  # the mock reports memory verbatim: used + free must be the total
        m = d["memory"]
        assert m["used_bytes"] + m["free_bytes"] == m["total_bytes"] == ex.FB_TOTAL_MIB["a100"] * 1024 * 1024
    assert dev[1]["memory"]["used_bytes"] > dev[0]["memory"]["used_bytes"] * 50


def test_only_the_first_gpu_node_and_the_xid_hits_gpu_zero_only():
    _ex, st = state()
    st.set_alloc({"gpu002": [0]})  # another node's job does not show on the real exporter
    assert all(d["utilization"]["gpu"] == 0 for d in st.dcgm_overrides()["devices"].values())
    st.inject({"node": "gpu001", "xid": 63})
    dev = st.dcgm_overrides()["devices"]
    assert dev[0]["failure"] == {"mode": "ecc_uncorrectable", "xid": {"code": 63}} and "failure" not in dev[1]
    st.inject({"node": "gpu001", "clear": True})
    assert "failure" not in st.dcgm_overrides()["devices"][0]


def test_a_pinned_temperature_applies_and_a_cluster_without_gpus_writes_nothing():
    ex, st = state()
    st.inject({"node": "gpu001", "temp": 91})
    assert st.dcgm_overrides()["devices"][1]["thermal"]["temperature_gpu_c"] == 91
    assert ex.State({"cn001": 0}).dcgm_overrides() == {}


def test_the_override_file_is_written_atomically_and_only_when_it_changes(tmp_path):
    import threading
    ex, st = state()
    path = tmp_path / "overrides.yaml"
    stop = threading.Event()
    t = threading.Thread(target=ex.write_dcgm, args=(st, str(path), 0.05, stop), daemon=True)
    t.start()
    try:
        for _ in range(100):
            if path.exists():
                break
            stop.wait(0.05)
        assert yaml.safe_load(path.read_text())["devices"][0]["utilization"]["gpu"] == 0
        st.set_alloc({"gpu001": [0]})
        for _ in range(100):
            if yaml.safe_load(path.read_text())["devices"][0]["utilization"]["gpu"] > 70:
                break
            stop.wait(0.05)
        assert yaml.safe_load(path.read_text())["devices"][0]["utilization"]["gpu"] > 70
        assert not (tmp_path / "overrides.yaml.tmp").exists()
    finally:
        stop.set()
