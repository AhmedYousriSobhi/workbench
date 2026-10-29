import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("bmc", ROOT / "components/bmc-farm/bmc.py")
bmc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bmc)


@pytest.fixture
def server():
    nodes = bmc.load_nodes(ROOT / "sim.yaml")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bmc.make_handler(bmc.Farm(nodes)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", nodes
    srv.shutdown()


def call(url, body=None):
    req = Request(url, data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST")
    with urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_nodes_match_sim_yaml(server):
    base, nodes = server
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    assert len(nodes) == sim["compute"]["count"] + sim["gpu"]["nodes"]
    assert call(f"{base}/healthz")["nodes"] == len(nodes)


def test_system_and_thermal_resources(server):
    base, nodes = server
    gpu = next(n for n, v in nodes.items() if v["gpus"])
    s = call(f"{base}/{gpu}/redfish/v1/Systems/1")
    assert s["PowerState"] == "On" and s["Oem"]["GpuCount"] == nodes[gpu]["gpus"]
    t = call(f"{base}/{gpu}/redfish/v1/Chassis/1/Thermal")
    assert len(t["Fans"]) == 6 and t["Temperatures"][0]["ReadingCelsius"] < 90


def test_injection_changes_health_and_logs(server):
    base, nodes = server
    n = next(iter(nodes))
    call(f"{base}/sim/inject", {"node": n, "temp": 95, "fan_fail": 2})
    t = call(f"{base}/{n}/redfish/v1/Chassis/1/Thermal")
    assert t["Temperatures"][0]["ReadingCelsius"] == 95.0
    assert [f["Reading"] for f in t["Fans"][:2]] == [0, 0] and t["Fans"][2]["Reading"] > 0
    assert call(f"{base}/{n}/redfish/v1/Systems/1")["Status"]["Health"] == "Critical"
    log = call(f"{base}/{n}/redfish/v1/Systems/1/LogServices/Log/Entries")
    assert log["Members@odata.count"] == 2
    call(f"{base}/sim/inject", {"node": n, "clear": True})
    assert call(f"{base}/{n}/redfish/v1/Systems/1")["Status"]["Health"] == "OK"


def test_reset_action_changes_power_state(server):
    base, nodes = server
    n = next(iter(nodes))
    call(f"{base}/{n}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset", {"ResetType": "ForceOff"})
    assert call(f"{base}/{n}/redfish/v1/Systems/1")["PowerState"] == "Off"
    assert call(f"{base}/{n}/redfish/v1/Chassis/1/Power")["PowerControl"][0]["PowerConsumedWatts"] == 0


def test_bad_requests_are_rejected(server):
    from urllib.error import HTTPError
    base, nodes = server
    n = next(iter(nodes))
    for url, body in ((f"{base}/sim/inject", {"node": "nope"}),
                      (f"{base}/{n}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset", {"ResetType": "Explode"})):
        with pytest.raises(HTTPError) as e:
            call(url, body)
        assert e.value.code == 400
    with pytest.raises(HTTPError) as e:
        call(f"{base}/nope/redfish/v1/Systems/1")
    assert e.value.code == 404
