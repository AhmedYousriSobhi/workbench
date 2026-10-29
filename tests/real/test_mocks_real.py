"""bcm-mock and jira-mock inside the live min stack: workflow errors, JQL, webhooks, failure modes, power, injection.

The unit tests run the same code in-process; this proves the built containers behave the same.
"""
import json
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote

import pytest
import yaml
from helpers import bring_up, wait_for

from simctl import stack

ROOT = Path(__file__).resolve().parents[2]
JIRA, BCM = "http://127.0.0.1:8080", "http://127.0.0.1:8081"


def call(url: str, body: dict | None = None) -> tuple[int, dict | None, dict]:
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None, dict(r.headers)
    except HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


def ready(url: str) -> bool:
    try:
        return call(url)[0] == 200
    except OSError:
        return False


def gateway() -> str:
    """The host's address on the compose network: where containers can reach a receiver on the host."""
    out = subprocess.run(["docker", "network", "inspect", "hpcsim_default", "-f", "{{(index .IPAM.Config 0).Gateway}}"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


@pytest.fixture(scope="module")
def mocks():
    import os
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(lambda: ready(f"{JIRA}/healthz") and ready(f"{BCM}/healthz"), timeout=30)
    yield
    stack.down(say=lambda m: None)


def issue(summary: str = "t", **fields) -> str:
    code, body, _ = call(f"{JIRA}/rest/api/2/issue", {"fields": {"project": {"key": "LIVE"}, "summary": summary, **fields}})
    assert code == 201
    return body["key"]


def test_workflow_requires_resolution_in_the_container(mocks):
    key = issue()
    code, body, _ = call(f"{JIRA}/rest/api/2/issue/{key}/transitions", {"transition": {"id": "31"}})
    assert code == 400 and body["errors"] == {"resolution": "Resolution is required."}
    code, _, _ = call(f"{JIRA}/rest/api/2/issue/{key}/transitions",
                      {"transition": {"id": "31"}, "fields": {"resolution": {"name": "Fixed"}}})
    assert code == 204
    assert call(f"{JIRA}/rest/api/2/issue/{key}")[1]["fields"]["status"]["name"] == "Resolved"


def test_jql_in_the_container(mocks):
    key = issue("jql", labels=["live-gpu"])
    issue("other", labels=["live-net"])
    hits = call(f"{JIRA}/rest/api/2/search?jql=" + quote("project = LIVE AND labels = live-gpu"))[1]["issues"]
    assert [i["key"] for i in hits] == [key]
    assert call(f"{JIRA}/rest/api/2/search?jql=" + quote("nosuchfield = x"))[0] == 400


def test_failure_modes_in_the_container(mocks):
    assert call(f"{JIRA}/sim/failure", {"mode": "429", "count": 2})[0] == 200
    code, _, headers = call(f"{JIRA}/rest/api/2/issue", {"fields": {"project": {"key": "LIVE"}, "summary": "x"}})
    assert code == 429 and headers["Retry-After"] == "1"
    assert call(f"{JIRA}/rest/api/2/search?jql=" + quote("project = LIVE"))[0] == 429
    assert call(f"{JIRA}/rest/api/2/search?jql=" + quote("project = LIVE"))[0] == 200  # count spent
    call(f"{JIRA}/sim/failure", {"mode": "503", "count": 1})
    assert call(f"{JIRA}/rest/api/2/issue", {"fields": {}})[0] == 503
    assert call(f"{JIRA}/healthz")[0] == 200


def test_webhook_reaches_a_receiver_outside_the_container(mocks):
    got: list[dict] = []

    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    gw = gateway()
    srv = HTTPServer((gw, 0), Hook)  # the compose gateway only, never 0.0.0.0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert call(f"{JIRA}/rest/webhooks/1.0/webhook", {"url": f"http://{gw}:{srv.server_port}/h"})[0] == 201
        key = issue("hooked")
        assert wait_for(lambda: any(p["webhookEvent"] == "jira:issue_created" and p["issue"]["key"] == key for p in got))
    finally:
        srv.shutdown()


def test_bcm_power_and_injection_in_the_container(mocks):
    def rpc(service, call_, *args):
        return call(f"{BCM}/json", {"service": service, "call": call_, "args": list(args)})[1]

    assert len(rpc("cmdevice", "getDevices")["result"]) == 6
    assert rpc("cmdevice", "powerOff", "cn004")["result"]["state"] == "DOWN"
    assert call(f"{BCM}/sim/inject", {"node": "cn004", "closed": True})[0] == 200
    assert rpc("cmstatus", "getStatus", "cn004")["result"]["state"] == "CLOSED"
    assert "Unknown device" in rpc("cmdevice", "getDevice", "nope")["error"]


def test_monitoring_client_style_search_and_myself_in_the_container(mocks):
    """The calls a monitoring client makes: POST /search/jql with a grouped JQL, then /myself and /serverInfo."""
    key = issue("monitor", labels=["gpu-farm"])
    body = {"jql": '(project = "LIVE" AND (labels in ("gpu-farm") OR text ~ "zzz")) ORDER BY updated DESC',
            "maxResults": 25, "fields": ["summary", "status", "issuetype", "updated", "assignee"]}
    status, page, _ = call(f"{JIRA}/rest/api/3/search/jql", body)
    assert status == 200 and key in [i["key"] for i in page["issues"]] and page["isLast"] is True
    assert call(f"{JIRA}/rest/api/2/myself")[1]["accountId"] == "alice-id"
    assert call(f"{JIRA}/rest/api/2/serverInfo")[1]["version"]
