import json
from pathlib import Path

import pytest
import yaml

from webui import cluster

SIM = yaml.safe_load((Path(__file__).resolve().parent.parent / "sim.yaml").read_text())


def make_fetch(power=None, temps=None, fans=None, bcm=None, xid="", down=()):
    """A fake of http_json answering like the BMC farm, BCM mock and GPU exporter."""
    power, temps, fans, bcm = power or {}, temps or {}, fans or {}, bcm or {}

    def fetch(url, body=None, timeout=2.0):
        if any(d in url for d in down):
            raise OSError("refused")
        if "/healthz" in url:
            return {"ok": True}
        if url.endswith(":9400/metrics"):
            return xid
        if url.endswith("/json"):
            return {"result": [{"hostname": n, "status": s} for n, s in bcm.items()]}
        node = url.split("/")[3]
        if url.endswith("Systems/1"):
            return {"PowerState": power.get(node, "On")}
        return {"Temperatures": [{"Name": "CPU", "ReadingCelsius": temps.get(node, 50.0)}, {"Name": "Inlet", "ReadingCelsius": 24}],
                "Fans": [{"Status": {"Health": "Critical" if i < fans.get(node, 0) else "OK"}} for i in range(6)]}

    return fetch


def test_xid_by_node_takes_the_highest_per_node():
    text = ('# HELP x\nDCGM_FI_DEV_XID_ERRORS{node="gpu001",gpu="0"} 0\nDCGM_FI_DEV_XID_ERRORS{node="gpu001",gpu="1"} 79\n'
            'DCGM_FI_DEV_XID_ERRORS{node="gpu002",gpu="0"} 0\nOTHER{node="gpu001"} 5\n')
    assert cluster.xid_by_node(text) == {"gpu001": 79, "gpu002": 0}


@pytest.mark.parametrize("row,expected", [
    ({"power": None, "temp": None, "fans_failed": 0, "xid": 0, "bcm": None}, ("unknown", "No data")),
    ({"power": "Off", "temp": 18, "fans_failed": 0, "xid": 0, "bcm": "DOWN"}, ("down", "Powered off")),
    ({"power": "On", "temp": 50, "fans_failed": 0, "xid": 79, "bcm": "UP"}, ("fault", "GPU error XID 79")),
    ({"power": "On", "temp": 92, "fans_failed": 0, "xid": 0, "bcm": "UP"}, ("fault", "Overheating (92 °C)")),
    ({"power": "On", "temp": 50, "fans_failed": 3, "xid": 0, "bcm": "UP"}, ("warn", "3 fan(s) failed")),
    ({"power": "On", "temp": 50, "fans_failed": 0, "xid": 0, "bcm": "CLOSED"}, ("warn", "BCM says CLOSED")),
    ({"power": "On", "temp": 50, "fans_failed": 0, "xid": 0, "bcm": "UP"}, ("ok", "Healthy")),
    ({"power": "On", "temp": 50, "fans_failed": 0, "xid": 0, "bcm": None}, ("ok", "Healthy")),  # BCM down is not the node's fault
])
def test_classify_picks_the_most_serious_condition(row, expected):
    assert cluster.classify(row) == expected


def test_most_serious_wins_when_several_things_are_wrong():
    both = {"power": "On", "temp": 95, "fans_failed": 2, "xid": 0, "bcm": "CLOSED"}
    assert cluster.classify(both)[0] == "fault"


def test_snapshot_combines_bmc_bcm_and_exporter():
    fetch = make_fetch(power={"cn002": "Off"}, temps={"gpu001": 93.0}, fans={"cn003": 2},
                       bcm={"cn001": "UP", "cn002": "DOWN"}, xid='DCGM_FI_DEV_XID_ERRORS{node="gpu002",gpu="0"} 79\n')
    snap = cluster.Cluster(SIM, fetch=fetch).snapshot()
    by = {n["name"]: n for n in snap["nodes"]}
    assert list(by) == ["cn001", "cn002", "cn003", "cn004", "gpu001", "gpu002"]
    assert by["cn001"]["state"] == "ok" and by["cn002"]["state"] == "down"
    assert (by["cn003"]["state"], by["cn003"]["fans_failed"]) == ("warn", 2)
    assert by["gpu001"]["label"].startswith("Overheating") and by["gpu002"]["label"] == "GPU error XID 79"
    assert by["gpu001"]["gpus"] == 8 and by["cn001"]["gpus"] == 0
    assert all(snap["services"].values())
    assert {a["id"] for a in snap["actions"]} == set(cluster.ACTIONS)


def test_snapshot_when_the_stack_is_down_is_all_unknown_not_an_error():
    def down(url, body=None, timeout=2.0):
        raise OSError("refused")

    snap = cluster.Cluster(SIM, fetch=down).snapshot()
    assert not any(snap["services"].values())
    assert {n["state"] for n in snap["nodes"]} == {"unknown"}


def test_one_service_down_only_marks_that_service():
    snap = cluster.Cluster(SIM, fetch=make_fetch(down=(":8081",))).snapshot()
    assert snap["services"]["bcm-mock"] is False and snap["services"]["bmc-farm"] is True
    assert snap["nodes"][0]["bcm"] is None and snap["nodes"][0]["state"] == "ok"


def test_actions_reuse_scenario_steps_with_the_node_filled_in():
    sent = []
    c = cluster.Cluster(SIM, fetch=make_fetch(), poster=lambda url, body: sent.append((url, body)))
    res = c.act("cn002", "power-off")
    assert [r["status"] for r in res] == ["ok", "ok"]
    assert sent == [("http://127.0.0.1:8000/sim/inject", {"node": "cn002", "power": "Off"}),
                    ("http://127.0.0.1:8081/sim/inject", {"node": "cn002", "state": "DOWN"})]
    sent.clear()
    c.act("gpu001", "gpu-xid")
    assert sent == [("http://127.0.0.1:9400/sim/inject", {"node": "gpu001", "xid": 79})]
    sent.clear()
    c.act("cn001", "clear")  # no GPU exporter call for a compute node
    assert [u for u, _ in sent] == ["http://127.0.0.1:8000/sim/inject", "http://127.0.0.1:8081/sim/inject"]
    sent.clear()
    c.act("gpu002", "clear")
    assert len(sent) == 3


def test_action_validation():
    c = cluster.Cluster(SIM, fetch=make_fetch())
    for node, action, msg in (("nope", "clear", "unknown node"), ("cn001", "explode", "unknown action"),
                              ("cn001", "gpu-xid", "only applies to GPU nodes")):
        with pytest.raises(cluster.ActionError, match=msg):
            c.act(node, action)


def test_action_reports_an_unreachable_mock():
    def refuse(url, body):
        raise ConnectionRefusedError

    res = cluster.Cluster(SIM, fetch=make_fetch(), poster=refuse).act("cn001", "overheat")
    assert res[0]["status"] == "failed" and "unreachable" in res[0]["detail"]


def test_http_json_parses_json_and_returns_text_otherwise(monkeypatch):
    class R:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cluster.urllib.request, "urlopen", lambda req, timeout: R(json.dumps({"a": 1}).encode()))
    assert cluster.http_json("http://x") == {"a": 1}
    monkeypatch.setattr(cluster.urllib.request, "urlopen", lambda req, timeout: R(b"# metrics\n"))
    assert cluster.http_json("http://x") == "# metrics\n"
