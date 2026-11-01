import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("bcm", ROOT / "components/bcm-mock/bcm.py")
bcm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bcm)


@pytest.fixture
def server():
    nodes = bcm.load_nodes(ROOT / "sim.yaml")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bcm.make_handler(bcm.Cluster(nodes)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", nodes
    srv.shutdown()


def post(base, path, body):
    req = Request(base + path, data=json.dumps(body).encode(), method="POST")
    try:
        with urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def rpc(base, service, call, *args):
    return post(base, "/json", {"service": service, "call": call, "args": list(args)})[1]


def test_devices_match_sim_yaml(server):
    base, nodes = server
    devices = rpc(base, "cmdevice", "getDevices")["result"]
    assert [d["hostname"] for d in devices] == list(nodes)
    assert {d["category"] for d in devices} == {"compute", "gpu"}
    assert len({d["ip"] for d in devices}) == len(devices)


def test_device_status_and_unknown(server):
    base, _ = server
    assert rpc(base, "cmdevice", "getDevice", "gpu001")["result"]["gpus"] == 8
    assert rpc(base, "cmstatus", "getStatus", "cn001")["result"]["state"] == "UP"
    assert "Unknown device" in rpc(base, "cmdevice", "getDevice", "nope")["error"]
    assert "Unknown call" in rpc(base, "cmdevice", "explode")["error"]
    assert [c["name"] for c in rpc(base, "cmpart", "getCategories")["result"]] == ["compute", "gpu"]


def test_power_calls_and_injection_change_state(server):
    base, _ = server
    assert rpc(base, "cmdevice", "powerOff", "cn002")["result"]["state"] == "DOWN"
    assert rpc(base, "cmstatus", "getStatus", "cn002")["result"]["state"] == "DOWN"
    assert post(base, "/sim/inject", {"node": "cn002", "closed": True})[0] == 200
    assert rpc(base, "cmstatus", "getStatus", "cn002")["result"]["state"] == "CLOSED"
    assert post(base, "/sim/inject", {"node": "cn002", "state": "BOGUS"})[0] == 400
    assert post(base, "/sim/inject", {"node": "zzz", "state": "UP"})[0] == 400
