"""Workload flow feed (#74): the mocks count client requests; webui.flow turns the counters into events."""
import importlib.util
import json
import random
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bmc = load("components/bmc-farm/bmc.py", "bmc_f")
bcm = load("components/bcm-mock/bcm.py", "bcm_f")
jira = load("components/jira-mock/jira.py", "jira_f")
exporter = load("components/gpu-exporter-farm/exporter.py", "exporter_f")
SIM = ROOT / "sim.yaml"


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


# (name, handler factory, a path a real client would request)
MOCKS = [
    ("bmc", lambda: bmc.make_handler(bmc.Farm(bmc.load_nodes(SIM))), "/cn001/redfish/v1/Systems/1"),
    ("bcm", lambda: bcm.make_handler(bcm.Cluster(bcm.load_nodes(SIM))), None),
    ("jira", lambda: jira.make_handler(jira.Jira(deliver=lambda *a: None, sleep=lambda s: None)),
     "/rest/api/2/search?jql=project%3DOPS"),
    ("exporter", lambda: exporter.make_handler(exporter.State(exporter.load_nodes(SIM), random.Random(1))), "/metrics"),
]


def get(url, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.mark.parametrize("name,make,path", MOCKS, ids=[m[0] for m in MOCKS])
def test_mock_counts_only_client_requests(name, make, path):
    srv, base = serve(make())
    try:
        assert json.loads(get(base + "/sim/stats")[1])["requests"] == 0
        for _ in range(2):  # two real client requests
            if name == "bcm":
                get(base + "/json", data=b'{"service": "cmdevice", "call": "getDevices"}')
            else:
                get(base + path)
        get(base + "/healthz")  # health check: not traffic
        get(base + (path or "/healthz"), headers={"X-Workbench-Probe": "1"})  # the UI's own poll: not traffic
        get(base + "/sim/inject", data=b"{}")  # control call: not traffic
        assert json.loads(get(base + "/sim/stats")[1])["requests"] == 2
    finally:
        srv.shutdown()


def test_ui_polls_carry_the_probe_header(monkeypatch):
    from webui import cluster
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen.update(req.header_items())
        raise OSError("stop here")
    monkeypatch.setattr(cluster.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OSError):
        cluster.http_json("http://127.0.0.1:1/x")
    assert seen.get("X-workbench-probe") == "1"


class FakeMocks:
    def __init__(self):
        from webui.flow import STATS
        self.by_url = {v: k for k, v in STATS.items()}
        self.counts = dict.fromkeys(STATS, 0)
        self.down = set()

    def __call__(self, url, *a):
        svc = self.by_url[url]
        if svc in self.down:
            raise OSError("down")
        return {"requests": self.counts[svc]}


def make_flow(fm, **kw):
    from webui.flow import Flow
    t = [0.0]
    f = Flow(fetch=fm, clock=lambda: t[0], **kw)
    f.tick = lambda dt=1.0: t.__setitem__(0, t[0] + dt)
    return f


def test_first_poll_only_baselines_then_a_rise_becomes_an_event():
    fm = FakeMocks()
    fm.counts["jira-mock"] = 40  # traffic from before the UI looked: not replayed
    f = make_flow(fm)
    f.poll()
    assert f.since(0)["events"] == []
    f.tick()
    fm.counts["jira-mock"] = 43
    f.poll()
    snap = f.since(0)
    assert snap["events"] == [{"id": 1, "from": "clients", "to": "jira-mock", "n": 3, "label": "requests", "nodes": []}]
    assert snap["totals"]["jira-mock"] == 3 and snap["cursor"] == 1 and all(snap["reachable"][k] for k in ("bmc-farm", "bcm-mock", "jira-mock", "gpu-exporter"))
    assert f.since(1)["events"] == []  # a client that has seen id 1 gets nothing new


def test_poll_is_rate_limited_and_survives_restart_and_outage():
    fm = FakeMocks()
    f = make_flow(fm, min_interval=0.8)
    f.poll()
    fm.counts["bmc-farm"] = 5
    f.tick(0.1)
    f.poll()  # too soon: ignored
    assert f.since(0)["events"] == []
    f.tick(1)
    f.poll()
    assert [e["n"] for e in f.since(0)["events"]] == [5]
    fm.counts["bmc-farm"] = 2  # the mock restarted: re-baseline, no negative event
    f.tick()
    f.poll()
    assert len(f.since(0)["events"]) == 1
    fm.down.add("bcm-mock")
    f.tick()
    f.poll()
    assert f.since(0)["reachable"]["bcm-mock"] is False
    fm.down.clear()
    fm.counts["bcm-mock"] = 9  # back up: baseline again, not a 9-request burst
    for _ in range(2):
        f.tick()
        f.poll()
    assert all(e["to"] != "bcm-mock" for e in f.since(0)["events"])


def test_notes_map_scenario_targets_and_ignore_unbuilt_ones():
    f = make_flow(FakeMocks())
    f.note("jira", "jira outage")
    f.note("bmc-farm", "temp")
    f.note("slurm", "drain")  # no block for it
    ev = f.since(0)["events"]
    assert [(e["from"], e["to"]) for e in ev] == [("injector", "jira-mock"), ("injector", "bmc-farm")]


def test_ring_buffer_is_bounded():
    f = make_flow(FakeMocks(), keep=5)
    for _ in range(20):
        f.note("jira", "x")
    assert len(f.since(0)["events"]) == 5 and f.since(0)["cursor"] == 20


def test_default_fetch_reaches_real_counters(monkeypatch):
    import webui.flow as wf
    srv, base = serve(MOCKS[2][1]())
    try:
        monkeypatch.setitem(wf.STATS, "jira-mock", base + "/sim/stats")
        monkeypatch.setitem(wf.STATS, "bmc-farm", "http://127.0.0.1:9/sim/stats")  # a closed port, whatever runs on the host
        f = wf.Flow()
        f.poll()
        snap = f.since(0)
        assert snap["reachable"]["jira-mock"] is True and snap["reachable"]["bmc-farm"] is False
    finally:
        srv.shutdown()


def stream(base, token, path, n=2):
    r = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"})
    out = []
    with urllib.request.urlopen(r, timeout=5) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        while len(out) < n:
            line = resp.readline().decode()
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def test_flow_endpoint_streams_and_pauses_at_l3(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    from governor import control
    from tests.test_webui import STATUS, TOKEN, free_port
    from webui.app import create_app
    level = [2]
    sock = str(tmp_path / "gov.sock")
    gov = control.serve(sock, lambda cmd, req: {**STATUS, "level": level[0]}, lambda: set())
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, sock, tmp_path, ROOT / "sim.yaml", interval=0.05),
                                           host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    try:
        a, _ = stream(base, TOKEN, "/api/flow")
        assert set(a) >= {"cursor", "events", "totals", "reachable", "paused", "level"} and a["paused"] is False
        level[0] = 3
        time.sleep(0.2)
        c, _ = stream(base, TOKEN, "/api/flow?after=0")
        assert c["paused"] is True and c["level"] == 3
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/flow", timeout=5)
        assert e.value.code == 401
    finally:
        server.should_exit = True
        gov.shutdown()


def test_scenario_steps_and_fault_buttons_become_injector_events(tmp_path, monkeypatch):
    import yaml

    from benchctl import scenario
    from webui.app import Runs
    (tmp_path / "t.yaml").write_text("name: t\ndescription: d\nsteps:\n  - {at: 0s, target: bmc-farm, do: {nodes: [cn001], temp: 90}}\n"
                                     "  - {at: 0s, target: slurm, do: {drain: [cn001]}}\n")
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    f = make_flow(FakeMocks())
    runs = Runs(tmp_path, sim, f)
    monkeypatch.setattr(scenario, "execute", lambda step, *a: scenario.Outcome(
        step.target, "skipped" if step.target == "slurm" else "ok", ""))
    runs.start("t", 1000)
    deadline = time.time() + 5
    while runs.state["running"] and time.time() < deadline:
        time.sleep(0.02)
    got = [(e["from"], e["to"]) for e in f.since(0)["events"]]
    assert got == [("injector", "bmc-farm")]  # the skipped slurm step draws nothing


def test_fault_events_carry_the_node_names_they_hit():
    f = make_flow(FakeMocks())
    f.note("bmc-farm", "power-off on cn002", ["cn002", 7])
    f.note("jira", "outage")
    ev = f.since(0)["events"]
    assert ev[0]["nodes"] == ["cn002"] and ev[1]["nodes"] == []


SCRAPE = """# HELP teleport_connect_to_node_attempts_total Number of connection attempts to a node
# TYPE teleport_connect_to_node_attempts_total counter
teleport_connect_to_node_attempts_total{type="ssh"} 3
teleport_connect_to_node_attempts_total{type="web"} 2
teleport_connect_to_node_attempts_total_extra 99
teleport_failed_connect_to_node_attempts_total 7
go_goroutines 41
"""


def test_teleport_traffic_is_the_proxy_connection_counter():
    from webui.flow import TELEPORT_COUNTER, counter_total
    assert counter_total(SCRAPE, TELEPORT_COUNTER) == 5  # both samples, not the longer name or the failed counter
    assert counter_total("go_goroutines 4\n", TELEPORT_COUNTER) == 0  # nothing counted yet
    with pytest.raises(TypeError):
        counter_total({"requests": 1}, TELEPORT_COUNTER)


def test_a_rise_in_proxy_connections_becomes_a_clients_to_teleport_event():
    from webui.flow import COUNTERS
    scrape = {"text": SCRAPE}

    def fetch(url, *a):
        if url == COUNTERS["teleport"]:
            return scrape["text"]
        raise OSError("down")
    f = make_flow(fetch)
    f.poll()
    assert f.since(0)["events"] == [] and f.since(0)["reachable"]["teleport"] is True
    f.tick()
    scrape["text"] = SCRAPE.replace('"ssh"} 3', '"ssh"} 6')
    f.poll()
    snap = f.since(0)
    assert snap["events"][0]["to"] == "teleport" and snap["events"][0]["n"] == 3 and snap["totals"]["teleport"] == 3
    f.tick()
    scrape["text"] = "not reachable"  # a scrape without the counter is reachable with nothing counted, never a crash
    f.poll()
    assert f.since(0)["reachable"]["teleport"] is True
