"""Tickets on the live stack (#107): a client's REST flow, and the ticket scenarios next to a real Slurm drain."""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import scenario, stack

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8080"


def call(path: str, body=None, method: str | None = None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 method=method or ("GET" if body is None else "POST"))
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        return r.status, json.loads(raw) if raw else None


def search(jql: str) -> list[dict]:
    return call("/rest/api/2/search?jql=" + urllib.parse.quote(jql))[1]["issues"]


@pytest.fixture(scope="module")
def live():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(lambda: call("/healthz")[0] == 200, timeout=40)
    assert wait_for(lambda: scenario.node_state("cn003") != "", timeout=60)
    call("/sim/tickets", {"action": "reset"})
    yield sim
    stack.down(say=lambda m: None)


def play(name: str, sim: dict, speed: float = 20):
    sc = scenario.load(name)
    assert scenario.validate(sc, sim) == []
    report = scenario.run(sc, speed=speed, dilation=float(sim.get("time_dilation", 1.0)), say=lambda m: None)
    assert report.ok, [o for o in report.steps + report.expects if o.status == "failed"]


def test_a_client_can_create_note_edit_watch_and_move_a_ticket(live):
    key = call("/rest/api/2/issue", {"fields": {"project": {"key": "OPS"}, "summary": "client flow", "labels": ["workbench"]}})[1]["key"]
    assert call(f"/rest/api/2/issue/{key}/comment", {"body": "first note"})[0] == 201
    assert call(f"/rest/api/2/issue/{key}", {"fields": {"priority": {"name": "High"}}}, "PUT")[0] == 204
    assert call(f"/rest/api/2/issue/{key}/watchers", "alice")[0] == 204
    assert call(f"/rest/api/2/issue/{key}/worklog", {"timeSpent": "30m"})[0] == 201
    moves = {t["name"]: t["id"] for t in call(f"/rest/api/2/issue/{key}/transitions")[1]["transitions"]}
    assert call(f"/rest/api/2/issue/{key}/transitions", {"transition": {"id": moves["Start Progress"]}})[0] == 204
    moves = {t["name"]: t["id"] for t in call(f"/rest/api/2/issue/{key}/transitions")[1]["transitions"]}
    assert call(f"/rest/api/2/issue/{key}/transitions", {"transition": {"id": moves["Resolve"]},
                                                          "fields": {"resolution": {"name": "Done"}}})[0] == 204
    issue = call(f"/rest/api/2/issue/{key}")[1]["fields"]
    assert issue["status"]["name"] == "Resolved" and issue["priority"]["name"] == "High"
    assert issue["watches"]["watchCount"] == 1 and len(issue["comment"]["comments"]) == 1


def test_drain_ticket_scenario_drains_the_node_files_the_ticket_and_resolves_it(live):
    play("drain-ticket", live)
    found = search("labels = workbench AND labels = cn003 AND labels = node-drain")
    assert len(found) == 1 and found[0]["fields"]["status"]["name"] == "Resolved"
    assert "cn003" in found[0]["fields"]["summary"]
    assert search('text ~ "cn003" AND labels = "workbench"')  # the client's documented JQL recipes find it
    assert wait_for(lambda: "drain" not in scenario.node_state("cn003"), timeout=30)  # and the node is back


def test_lifecycle_and_wave_and_flap_scenarios_run_clean(live):
    play("ticket-lifecycle", live)
    assert search("labels = lifecycle")[0]["fields"]["status"]["name"] == "Closed"
    play("ticket-wave", live)
    wave = call("/rest/api/2/search?jql=" + urllib.parse.quote("labels = wave") + "&maxResults=50")[1]["issues"]
    assert len(wave) == 12
    assert {i["fields"]["status"]["name"] for i in wave} == {"Open", "In Progress", "Resolved", "Closed"}
    play("jira-flap", live)
    assert len(search("labels = flap")) == 2
