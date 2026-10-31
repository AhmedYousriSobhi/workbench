import base64
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
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


# --- the standard /redfish/v1 surface a real client sees (TLS in the container; plain here) ---

def raw(url, method="GET", body=None, headers=None):
    req = Request(url, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers or {})
    with urlopen(req, timeout=5) as r:
        data = r.read()
        return r.status, dict(r.headers), json.loads(data) if data else None


@pytest.fixture
def secure():
    nodes = bmc.load_nodes(ROOT / "sim.yaml")
    farm = bmc.Farm(nodes)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bmc.make_handler(farm, bmc.Sessions(("ops", "pw"))))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", nodes
    srv.shutdown()


def login(base, host=None):
    h = {"Host": host} if host else {}
    status, headers, _ = raw(f"{base}/redfish/v1/SessionService/Sessions", "POST", {"UserName": "ops", "Password": "pw"}, h)
    assert status == 201
    return {"X-Auth-Token": headers["X-Auth-Token"], **h}, headers["Location"]


def test_service_root_is_open_everything_else_needs_auth(secure):
    base, _ = secure
    assert raw(f"{base}/redfish/v1")[2]["RedfishVersion"]
    for path in ("/redfish/v1/Systems", "/redfish/v1/Chassis/1/Thermal"):
        with pytest.raises(HTTPError) as e:
            raw(base + path)
        assert e.value.code == 401


def test_bad_login_is_refused_and_basic_auth_works(secure):
    base, _ = secure
    with pytest.raises(HTTPError) as e:
        raw(f"{base}/redfish/v1/SessionService/Sessions", "POST", {"UserName": "ops", "Password": "no"})
    assert e.value.code == 401
    basic = {"Authorization": "Basic " + base64.b64encode(b"ops:pw").decode()}
    assert raw(f"{base}/redfish/v1/Systems", headers=basic)[0] == 200
    bad = {"Authorization": "Basic " + base64.b64encode(b"ops:x").decode()}
    with pytest.raises(HTTPError):
        raw(f"{base}/redfish/v1/Systems", headers=bad)


def test_walk_like_a_client_follows_links_to_every_reading(secure):
    base, _ = secure
    h, _ = login(base)
    get = lambda path: raw(base + path, headers=h)[2]
    first = lambda coll: get(get(coll)["Members"][0]["@odata.id"])
    system, chassis, manager = first("/redfish/v1/Systems"), first("/redfish/v1/Chassis"), first("/redfish/v1/Managers")
    assert system["Manufacturer"] and system["SerialNumber"] and system["PowerState"] == "On"
    assert manager["FirmwareVersion"] and chassis["Thermal"]["@odata.id"].startswith("/redfish/v1/")
    assert get(chassis["Thermal"]["@odata.id"])["Fans"] and get(chassis["Power"]["@odata.id"])["PowerControl"]
    fw = get(get("/redfish/v1/UpdateService")["FirmwareInventory"]["@odata.id"])["Members"]
    assert {get(m["@odata.id"])["Version"] for m in fw}
    acct = get(get(get("/redfish/v1/AccountService")["Accounts"]["@odata.id"])["Members"][0]["@odata.id"])
    assert acct["RoleId"] == "ReadOnly"
    logs = get(system["LogServices"]["@odata.id"])["Members"][0]["@odata.id"]
    assert "Members" in get(get(logs)["Entries"]["@odata.id"])


def test_host_header_selects_the_node_and_unknown_hosts_get_the_first(secure):
    base, nodes = secure
    a, b = next(iter(nodes)), list(nodes)[-1]
    ha, _ = login(base, f"bmc-{a}:8443")
    hb, _ = login(base, f"bmc-{b}")
    assert raw(f"{base}/redfish/v1/Systems/1", headers=ha)[2]["HostName"] == a
    assert raw(f"{base}/redfish/v1/Systems/1", headers=hb)[2]["HostName"] == b
    assert raw(f"{base}/redfish/v1/Systems/1", headers={**ha, "Host": "127.0.0.1:8443"})[2]["HostName"] == a


def test_injected_faults_show_on_the_standard_surface_and_session_ends(secure):
    base, nodes = secure
    n = next(iter(nodes))
    h, where = login(base, f"bmc-{n}")
    call(f"{base}/sim/inject", {"node": n, "fan_fail": 2, "event": "PSU 2 lost", "severity": "Critical"})
    assert raw(f"{base}/redfish/v1/Systems/1", headers=h)[2]["Status"]["Health"] == "Critical"
    entries = raw(f"{base}/redfish/v1/Systems/1/LogServices/Log/Entries", headers=h)[2]["Members"]
    assert any(e["Message"] == "PSU 2 lost" and e["@odata.id"].startswith("/redfish/v1/") for e in entries)
    assert raw(base + where, "DELETE", headers=h)[0] == 204
    with pytest.raises(HTTPError) as e:
        raw(f"{base}/redfish/v1/Systems", headers=h)
    assert e.value.code == 401


def test_no_credentials_file_means_auth_is_off(tmp_path):
    assert bmc.read_credentials(tmp_path / "missing") is None
    (tmp_path / "c").write_text("ops:secret\n")
    assert bmc.read_credentials(tmp_path / "c") == ("ops", "secret")
    assert bmc.Sessions(None).allows({}) is True


def test_sessions_are_capped():
    s = bmc.Sessions(("u", "p"))
    first, _ = s.login("u", "p")
    for _ in range(bmc.Sessions.MAX):
        s.login("u", "p")
    assert first not in s.tokens and len(s.tokens) == bmc.Sessions.MAX


def test_bmc_also_listens_on_the_standard_redfish_port_inside_the_cluster():
    src = (ROOT / "components" / "bmc-farm" / "bmc.py").read_text()
    assert "STANDARD_TLS_PORT = 443" in src and "(tls_port, STANDARD_TLS_PORT)" in src
    # not published on the host: binding 443 there needs root (NF-5)
    from benchctl import compose
    ports = compose.generate({"compute": {"prefix": "cn", "count": 1, "cpus": 2, "memory_mb": 100}, "gpu": {}}, {"groups": ["core", "mocks"]})["services"]["bmc-farm"]["ports"]
    assert ports == ["127.0.0.1:8000:8000", "127.0.0.1:8443:8443"]
