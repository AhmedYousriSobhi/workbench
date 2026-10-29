"""Error paths and entry points of the three mock servers."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bmc = load("components/bmc-farm/bmc.py", "bmc_e")
bcm = load("components/bcm-mock/bcm.py", "bcm_e")
jira = load("components/jira-mock/jira.py", "jira_e")


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def call(url, body=None, raw=None, method=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = Request(url, data=data, method=method or ("GET" if data is None else "POST"))
    try:
        with urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except HTTPError as e:
        return e.code, e.read()


@pytest.fixture
def bmc_url():
    srv, url = serve(bmc.make_handler(bmc.Farm(bmc.load_nodes(ROOT / "sim.yaml"))))
    yield url
    srv.shutdown()


@pytest.fixture
def bcm_url():
    srv, url = serve(bcm.make_handler(bcm.Cluster(bcm.load_nodes(ROOT / "sim.yaml"))))
    yield url
    srv.shutdown()


@pytest.fixture
def jira_url():
    j = jira.Jira(deliver=lambda *a: None)
    srv, url = serve(jira.make_handler(j))
    yield url
    srv.shutdown()


def test_bmc_power_event_and_reset_actions(bmc_url):
    assert call(f"{bmc_url}/sim/inject", {"node": "cn001", "power": "Off"})[0] == 200
    assert json.loads(call(f"{bmc_url}/cn001/redfish/v1/Systems/1")[1])["PowerState"] == "Off"
    assert call(f"{bmc_url}/sim/inject", {"node": "cn001", "event": "disk degraded", "severity": "Critical"})[0] == 200
    log = json.loads(call(f"{bmc_url}/cn001/redfish/v1/Systems/1/LogServices/Log/Entries")[1])
    assert any(m["Message"] == "disk degraded" and m["Severity"] == "Critical" for m in log["Members"])
    reset = f"{bmc_url}/cn001/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
    assert call(reset, {"ResetType": "On"})[0] == 200
    assert json.loads(call(f"{bmc_url}/cn001/redfish/v1/Systems/1")[1])["PowerState"] == "On"
    assert call(reset, {"ResetType": "Explode"})[0] == 400  # not a Redfish reset type
    assert call(f"{bmc_url}/cn001/redfish/v1/Nothing", {})[0] == 404
    assert call(f"{bmc_url}/sim/inject", {"node": "zzz", "power": "On"})[0] == 400


def test_bcm_invalid_json_and_unknown_paths(bcm_url):
    assert call(f"{bcm_url}/json", raw=b"{broken")[0] == 400
    assert call(f"{bcm_url}/elsewhere", {})[0] == 404
    assert call(f"{bcm_url}/elsewhere")[0] == 404
    assert call(f"{bcm_url}/healthz")[0] == 200


def test_jira_bad_requests_and_unknown_routes(jira_url):
    assert call(f"{jira_url}/rest/api/2/issue", raw=b"{broken")[0] == 400
    assert call(f"{jira_url}/rest/webhooks/1.0/webhook", {})[0] == 400  # url required
    code, body = call(f"{jira_url}/rest/webhooks/1.0/webhook", {"url": "http://r/h"})
    assert code == 201 and "jira:issue_created" in json.loads(body)["events"]  # default event set
    assert call(f"{jira_url}/nope")[0] == 404
    assert call(f"{jira_url}/rest/api/2/nothing")[0] == 404
    assert call(f"{jira_url}/rest/api/2/issue", {"fields": {"project": {"key": "A"}, "summary": "s"}})[0] == 201
    assert call(f"{jira_url}/rest/api/2/issue/A-1/bogus", {})[0] == 404


def test_jql_negation_and_empty_clauses():
    assert jira.parse_jql("") == [] and jira.parse_jql("ORDER BY created") == []  # no filter: everything matches
    clauses = jira.parse_jql("status not in (Open, Closed) AND project = OPS")
    assert clauses == [("status", "not in", ["Open", "Closed"]), ("project", "=", ["OPS"])]
    issue = {"key": "OPS-1", "fields": {"project": {"key": "OPS"}, "status": {"name": "Resolved"},
                                         "issuetype": {"name": "Task"}, "labels": [], "assignee": None}}
    assert jira.match(issue, "status", "not in", ["Open", "Closed"]) is True
    assert jira.match(issue, "status", "!=", ["Resolved"]) is False
    assert jira.match(issue, "assignee", "=", [""]) is True  # unassigned


def test_post_webhook_delivers_and_swallows_a_dead_receiver():
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/h"
    jira.post_webhook(url, {"webhookEvent": "x"})
    srv.shutdown()
    assert got["body"] == {"webhookEvent": "x"}
    jira.post_webhook(url, {"webhookEvent": "y"})  # receiver gone: must not raise


@pytest.mark.parametrize("mod,argv,default_port", [(bmc, ["bmc.py"], 8000), (bcm, ["bcm.py"], 8081),
                                                    (jira, ["jira.py"], 8080)])
def test_entry_points_bind_all_interfaces_on_their_default_port(monkeypatch, mod, argv, default_port):
    bound = {}

    class FakeServer:
        def __init__(self, addr, handler):
            bound["addr"] = addr

        def serve_forever(self):
            bound["served"] = True

    monkeypatch.setattr(mod, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(mod.sys, "argv", argv + ([str(ROOT / "sim.yaml")] if mod is not jira else []))
    mod.main()
    assert bound == {"addr": ("0.0.0.0", default_port), "served": True}  # inside the container; compose publishes on 127.0.0.1


def test_entry_point_port_argument_is_honoured(monkeypatch):
    bound = {}
    monkeypatch.setattr(jira, "ThreadingHTTPServer", lambda addr, h: type("S", (), {"serve_forever": lambda s: bound.update(a=addr)})())
    monkeypatch.setattr(jira.sys, "argv", ["jira.py", "9999"])
    jira.main()
    assert bound["a"] == ("0.0.0.0", 9999)
