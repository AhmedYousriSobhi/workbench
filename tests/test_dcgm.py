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
    assert env["MOCK_NVML_GPUS"] == str(SIM["gpu"]["gpus_per_node"])
    assert env["MOCK_NVML_PROFILE"].endswith(f"mock-nvml-config-{dcgm.profile(SIM)}.yaml")
    assert svc["environment"]["DCGM_HOSTNAME"] == "gpu001" and "hostname" not in svc and svc["ports"] == [f"127.0.0.1:{dcgm.PORT}:9400"]
    assert svc["mem_limit"] == f"{dcgm.LIMIT_MB}m" and svc["labels"]["workbench.class"] == "P6"
    assert not {"privileged", "cap_add", "security_opt"} & set(svc)  # stays unprivileged


@pytest.mark.parametrize("gpu_type,want", [("a100", "a100"), ("H200", "h100"), ("l40s", "l40s"), ("", "h100"), ("x9", "h100")])
def test_the_profile_follows_the_gpu_type(gpu_type, want):
    assert dcgm.profile({"gpu": {"type": gpu_type}}) == want


def test_it_needs_a_gpu_node():
    assert dcgm.problems(SIM) == [] and dcgm.node(SIM) == "gpu001"
    assert "GPU node" in dcgm.problems({"gpu": {"nodes": 0, "prefix": "gpu"}})[0]


def test_prepare_makes_a_directory_the_farm_can_write(tmp_path):
    dcgm.prepare(tmp_path, SIM)
    assert (tmp_path / "dcgm").stat().st_mode & 0o777 == 0o777 and (tmp_path / "dcgm" / "gpu001.yaml").exists()


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
    path = tmp_path / "gpu001.yaml"
    stop = threading.Event()
    t = threading.Thread(target=ex.write_dcgm, args=(st, str(tmp_path), 0.05, stop), daemon=True)
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
        assert not (tmp_path / "gpu001.yaml.tmp").exists()
    finally:
        stop.set()


def test_prepare_survives_a_file_owned_by_someone_else(tmp_path, monkeypatch):
    dcgm.prepare(tmp_path, SIM)
    real = Path.chmod

    def deny(self, mode, **kw):
        if self.name == "gpu001.yaml":
            raise PermissionError(self)
        return real(self, mode, **kw)
    monkeypatch.setattr(Path, "chmod", deny)
    dcgm.prepare(tmp_path, SIM)  # the second `benchctl up`: the farm's user owns the file


# ---- issue 435: more than one node, the model name, down and clear ----

MANY = {**SIM, "gpu": {**SIM["gpu"], "nodes": 3, "type": "h200"}, "dcgm": {"nodes": 3}}


def many_services():
    return compose.generate(MANY, {"groups": ["core", "dcgm"], "compute_nodes": 1})["services"]


def test_dcgm_nodes_gives_each_node_its_own_exporter_port_and_override_file():
    sv = many_services()
    assert dcgm.nodes(MANY) == ["gpu001", "gpu002", "gpu003"]
    assert [dcgm.service(MANY, n) for n in dcgm.nodes(MANY)] == ["dcgm-exporter", "dcgm-exporter-gpu002", "dcgm-exporter-gpu003"]
    assert [sv[dcgm.service(MANY, n)]["ports"] for n in dcgm.nodes(MANY)] == [["127.0.0.1:9402:9400"], ["127.0.0.1:9410:9400"], ["127.0.0.1:9411:9400"]]
    for i, n in enumerate(dcgm.nodes(MANY)):
        svc = sv[dcgm.service(MANY, n)]
        assert svc["environment"]["DCGM_HOSTNAME"] == n and "hostname" not in svc and svc["environment"]["MOCK_NVML_OVERRIDES"] == f"/dcgm/{n}.yaml" and svc["environment"]["MOCK_NVML_NODE_INDEX"] == str(i)
        assert "build" not in svc or n == "gpu001"  # the image is built once
        assert svc["mem_limit"] == f"{dcgm.LIMIT_MB}m" and not {"privileged", "cap_add"} & set(svc)
    assert dcgm.container(MANY, "gpu002") == "workbench-dcgm-exporter-gpu002-1" and dcgm.container(MANY, "cn001") is None


def test_h200_shows_its_own_model_name_and_a_type_with_a_profile_does_not_need_one():
    assert many_services()["dcgm-exporter"]["environment"]["MOCK_NVML_NAME"] == "NVIDIA H200"
    assert "MOCK_NVML_NAME" not in services(True)["dcgm-exporter"]["environment"] or SIM["gpu"]["type"] == "h200"
    one = compose.generate({**SIM, "gpu": {**SIM["gpu"], "type": "a100"}}, {"groups": ["core", "dcgm"], "compute_nodes": 1})["services"]
    assert "MOCK_NVML_NAME" not in one["dcgm-exporter"]["environment"]


def test_the_count_is_bounded_by_the_gpu_nodes_and_the_limit():
    assert dcgm.problems({**MANY, "dcgm": {"nodes": 4}})[0].startswith("--dcgm-nodes 4 is more than the GPU nodes")
    assert "1 to 8" in dcgm.problems({**MANY, "dcgm": {"nodes": 9}})[0]
    assert workbench.Spec(dcgm_nodes=2, parts=["dcgm"], gpu_nodes=2).problems() == []
    assert any("needs the dcgm part" in p for p in workbench.Spec(dcgm_nodes=2).problems())
    assert any("more than the GPU nodes" in p for p in workbench.Spec(dcgm_nodes=3, parts=["dcgm"], gpu_nodes=2).problems())
    sim, _ = workbench.effective(SIM, workbench.Spec(dcgm_nodes=2, parts=["dcgm"], gpu_nodes=2))
    assert sim["dcgm"] == {"nodes": 2}


def test_each_exporter_memory_is_the_measured_one():
    from benchctl import memory
    assert memory.key_of("dcgm-exporter-gpu002") == "dcgm-exporter"


def test_each_node_gets_its_own_override_file_and_an_inject_writes_it_before_answering(tmp_path):
    ex, st = state()
    last: dict[str, str] = {}
    ex.write_dcgm_once(st, str(tmp_path), last)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gpu001.yaml", "gpu002.yaml"]  # compute nodes have none
    st.inject({"node": "gpu002", "xid": 79})
    ex.write_dcgm_once(st, str(tmp_path), last)
    assert yaml.safe_load((tmp_path / "gpu002.yaml").read_text())["devices"][0]["failure"]["xid"]["code"] == 79
    assert "failure" not in yaml.safe_load((tmp_path / "gpu001.yaml").read_text())["devices"][0]
    assert st.dcgm_overrides("gpu002")["devices"][0]["failure"]["xid"]["code"] == 79


def test_a_dead_node_stops_its_real_exporter_and_a_clear_restarts_it():
    from benchctl import scenario
    step = scenario.Step

    def acts(target, do):
        return [(e.do["nodes"], [k for k in ("stop", "start", "restart") if e.do.get(k)]) for e in scenario.side_effects(step(0, target, do)) if e.target == "dcgm"]
    assert acts("bmc-farm", {"nodes": ["gpu001"], "power": "Off"}) == [(["gpu001"], ["stop"])]
    assert acts("bmc-farm", {"nodes": ["gpu001"], "power": "On"}) == [(["gpu001"], ["start"])]
    assert acts("bmc-farm", {"nodes": ["gpu001"], "clear": True}) == [(["gpu001"], ["restart"])]
    assert acts("gpu-exporter", {"nodes": ["gpu001"], "down": True}) == [(["gpu001"], ["stop"])]
    assert acts("gpu-exporter", {"nodes": ["gpu001"], "down": False}) == [(["gpu001"], ["start"])]
    assert acts("gpu-exporter", {"nodes": ["gpu001"], "clear": True}) == [(["gpu001"], ["restart"])]
    assert acts("gpu-exporter", {"nodes": ["gpu001"], "xid": 63}) == []


def test_the_dcgm_step_runs_docker_only_for_nodes_that_have_an_exporter(monkeypatch):
    from types import SimpleNamespace

    from benchctl import k8s, scenario
    monkeypatch.setattr(k8s, "current_sim", lambda: MANY)
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    out = scenario.execute(scenario.Step(0, "dcgm", {"nodes": ["gpu002", "cn001"], "restart": True}), poster=lambda *a, **k: None, runner=runner)
    assert out.status == "ok" and calls == [["docker", "restart", "-t", "1", "workbench-dcgm-exporter-gpu002-1"]]
    calls.clear()
    out = scenario.execute(scenario.Step(0, "dcgm", {"nodes": ["cn001"], "stop": True}), poster=lambda *a, **k: None, runner=runner)
    assert out.status == "ok" and calls == []
    assert scenario.execute(scenario.Step(0, "dcgm", {"nodes": ["gpu001"], "stop": True}), host=False).status == "skipped"
    assert scenario.validate({"steps": [{"at": 0, "target": "dcgm", "do": {"nodes": ["gpu001"]}}]}, MANY)  # needs a verb
