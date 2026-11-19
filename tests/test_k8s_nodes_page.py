"""Kubernetes nodes are machines on the page, in `benchctl get|describe node` and as scenario BMC targets (issue 385)."""
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import yaml

from benchctl import completion, describe, inventory, k8s, scenario, workbench
from webui import cluster

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
ON = workbench.effective(SIM, workbench.Spec(parts=[*workbench.DEFAULT_PARTS, "k8s"]))[0]


def api(not_ready=()):
    def fetch(path, **kw):
        return {"items": [{"metadata": {"name": n}, "spec": {}, "status": {"conditions": [{"type": "Ready", "status": "False" if n in not_ready else "True"}]}}
                          for n, _ in k8s.node_list(ON)]}
    return fetch


def test_scenario_validation_knows_kubernetes_nodes():
    sc = {"name": "x", "steps": [{"at": "0s", "target": "bmc-farm", "do": {"nodes": ["kgpu001"], "power": "Off"}}]}
    assert scenario.validate(sc, ON) == []
    assert scenario.validate(sc, SIM) == ["step 1: unknown node 'kgpu001'"]  # the part is off: the node does not exist


def test_power_off_and_overheat_of_a_kubernetes_node_make_it_notready_and_clear_restores_it():
    def kube(do):
        return [(s.target, s.do) for s in scenario.side_effects(scenario.Step(0, "bmc-farm", do), ON) if s.target in ("k8s", "slurm")]
    assert kube({"nodes": ["kgpu001"], "power": "Off"}) == [("k8s", {"down": ["kgpu001"]})]  # and no Slurm step: it is not a Slurm node
    assert kube({"nodes": ["kgpu001"], "temp": 92}) == [("k8s", {"down": ["kgpu001"]})]
    assert kube({"nodes": ["kgpu001"], "clear": True}) == [("k8s", {"up": ["kgpu001"]})]
    assert kube({"nodes": ["kgpu001"], "power": "On"}) == [("k8s", {"up": ["kgpu001"]})]
    assert kube({"nodes": ["kgpu001"], "fan_fail": 3}) == []
    both = kube({"nodes": ["gpu001", "kgpu001"], "power": "Off"})
    assert ("slurm", {"power_off": ["gpu001"]}) in both and ("k8s", {"down": ["kgpu001"]}) in both
    assert kube({"nodes": ["gpu001"], "power": "Off"}) == [("slurm", {"power_off": ["gpu001"]})]  # a Slurm node: as before
    assert [t for t, _ in kube({"nodes": ["gpu001"], "temp": 92})] == []


def test_the_page_plays_the_kubernetes_step_as_skipped_not_failed():
    got = scenario.execute(scenario.Step(0, "bmc-farm", {"nodes": ["kgpu001"], "power": "Off"}), poster=lambda *a: None, host=False, sim=ON)
    assert got.status == "ok" and "k8s steps need the host CLI" in got.detail


def test_get_nodes_lists_kubernetes_nodes_with_the_api_state():
    rows = inventory.node_rows(ON, runner=lambda cmd: CompletedProcess(cmd, 0, "", ""), fetch=api(not_ready={"kgpu001"}))
    by = {r["name"]: r for r in rows}
    assert (by["kgpu001"]["kind"], by["kgpu001"]["state"]) == ("k8s-gpu", "NotReady") and by["kcn001"]["state"] == "Ready"
    assert by["gpu001"]["state"] == "absent"
    assert [r["name"] for r in inventory.node_rows(SIM, runner=lambda cmd: CompletedProcess(cmd, 0, "", ""))] == [n for n, _ in cluster.node_names(SIM)]


def test_completion_offers_kubernetes_nodes(monkeypatch):
    monkeypatch.setattr(completion, "_sim", lambda: ON)
    assert completion.nodes("kg") == ["kgpu001", "kgpu002"]


def test_describe_node_reads_a_kubernetes_node():
    def fetch(url, timeout=2.0):
        if "redfish/v1/Systems" in url:
            return {"PowerState": "Off"}
        if "Thermal" in url:
            return {"Temperatures": [{"Name": "CPU", "ReadingCelsius": 18}], "Fans": []}
        return "DCGM_FI_DEV_GPU_UTIL{gpu=\"0\",node=\"kgpu001\"} 0\n"
    info = describe.gather(ON, "kgpu001", fetch, lambda c: pytest.fail("a Kubernetes node is no container"), kube_fetch=api(not_ready={"kgpu001"}))
    assert info["kind"] == "k8s-gpu" and info["kubernetes"]["state"] == "NotReady" and info["bmc"]["power"] == "Off"
    assert "container" not in info and "slurm" not in info and "ssh" not in info and info["gpus"]["count"] == 8
    text = describe.render(info)
    assert "Kubernetes:  NotReady" in text and "kubectl describe node kgpu001" in text
    with pytest.raises(describe.UnknownNode):
        describe.gather(SIM, "kgpu001", fetch)


@pytest.mark.parametrize("api_s,hw,want", [
    (("ok", "Ready"), ("ok", "Healthy"), ("ok", "Ready")),
    (("fault", "NotReady"), ("down", "Powered off"), ("down", "Powered off")),
    (("fault", "NotReady"), ("fault", "Overheating (92 °C)"), ("fault", "NotReady (Overheating (92 °C))")),
    (("ok", "Ready"), ("warn", "3 fan(s) failed"), ("warn", "3 fan(s) failed")),
    (("unknown", "No data"), ("ok", "Healthy"), ("ok", "Healthy")),
    (("fault", "NotReady"), ("unknown", "No data"), ("fault", "NotReady")),
    (("warn", "Cordoned (no new pods)"), ("ok", "Healthy"), ("warn", "Cordoned (no new pods)")),
])
def test_a_kubernetes_card_is_the_worse_of_the_api_and_the_machine(api_s, hw, want):
    assert cluster.merge_k8s_state(api_s, hw) == want


def page(monkeypatch, not_ready=(), power=None):
    from test_cluster import make_fetch

    monkeypatch.setattr(cluster, "k8s_built", lambda: True)
    monkeypatch.setattr(k8s, "current_sim", lambda: ON)
    monkeypatch.setattr(k8s, "api_up", lambda *a, **k: True)
    real = k8s.node_rows  # its default fetch is bound at definition: give it the fake API
    monkeypatch.setattr(k8s, "node_rows", lambda sim, fetch=None: real(sim, api(not_ready)))
    base = make_fetch()
    power = power or {}

    def fetch(url, body=None, timeout=2.0):
        got = base(url, body, timeout)
        if url.endswith(":8000/sim/summary"):
            got.update({n: {"power": power.get(n, "On"), "temp": 92.0 if n in power and power[n] == "Hot" else 50.0, "fans_failed": 0}
                        for n, _ in k8s.node_list(ON)})
            for n, p in power.items():
                got[n]["power"] = "On" if p == "Hot" else p
        elif url.endswith(":9400/sim/summary"):
            got.update({n: {"gpu_util": [], "xid": 0, "ib_down": 0, "nic_flap": False} for n, _ in k8s.node_list(ON)})
        elif url.endswith("/json"):
            got["result"] += [{"hostname": n, "status": "DOWN" if power.get(n) == "Off" else "UP"} for n, _ in k8s.node_list(ON)]
        return got
    return cluster.Cluster(SIM, fetch=fetch)


def test_the_card_of_a_kubernetes_node_has_power_temperature_and_bcm(monkeypatch):
    snap = page(monkeypatch, not_ready={"kgpu002"}, power={"kgpu002": "Off"}).snapshot()
    by = {n["name"]: n for n in snap["nodes"]}
    assert by["kcn001"]["kind"] == "k8s" and (by["kcn001"]["power"], by["kcn001"]["temp"], by["kcn001"]["bcm"]) == ("On", 50.0, "UP")
    assert by["kcn001"]["state"] == "ok" and by["kgpu001"]["gpus"] == 8
    assert (by["kgpu002"]["state"], by["kgpu002"]["label"], by["kgpu002"]["bcm"], by["kgpu002"]["power"]) == ("down", "Powered off", "DOWN", "Off")


def test_a_hot_kubernetes_node_reads_notready_with_the_reason(monkeypatch):
    snap = page(monkeypatch, not_ready={"kcn001"}, power={"kcn001": "Hot"}).snapshot()
    got = next(n for n in snap["nodes"] if n["name"] == "kcn001")
    assert got["state"] == "fault" and got["label"] == "NotReady (Overheating (92 °C))"


def test_actions_apply_to_kubernetes_nodes(monkeypatch):
    sent = []
    c = page(monkeypatch)
    c.poster = lambda url, body: sent.append((url, body))
    res = c.act("kgpu001", "power-off")
    assert [r["target"] for r in res] == ["bmc-farm"] and sent[0][1] == {"node": "kgpu001", "power": "Off"}
    assert {s[1]["node"] for s in sent} == {"kgpu001"}  # bcm and exporter follow; no Slurm step
    sent.clear()
    c.act("kgpu001", "gpu-xid")
    assert sent[0] == ("http://127.0.0.1:9400/sim/inject", {"node": "kgpu001", "xid": 79})
    with pytest.raises(cluster.ActionError, match="only applies to GPU nodes"):
        c.act("kcn001", "gpu-xid")
    with pytest.raises(cluster.ActionError, match="unknown node"):
        c.act("kzz999", "clear")
    with pytest.raises(AssertionError, match="unexpected call"):  # a known node: it asks the Jira mock (the fake has none), no KeyError
        c.tickets("kgpu001")
    with pytest.raises(KeyError):
        c.tickets("kzz999")
    assert json.dumps(c.live_sim()["kubernetes"]["enabled"]) == "true"
