"""The real Jira next to the mock (ADR 0026, issue 284): start the part, finish its setup, run a real round trip.

Needs Atlassian's published 3-hour Data Center test licence (docs/guides/jira-real.md) and about 2.4 GB of free memory;
without the licence file every test here skips and says where to put it. Slow (minutes: image pull, JVM start, wizard)."""
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from helpers import wait_for

from benchctl import jira_real, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
REAL = jira_real.URL
MOCK = "http://127.0.0.1:8080"


def benchctl(*args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts/benchctl"), *args], capture_output=True, text=True, timeout=timeout, check=False)


def call(base: str, path: str, body=None, method: str | None = None, token: str | None = None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method=method or ("GET" if body is None else "POST"),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


@pytest.fixture(scope="module")
def live():
    os.chdir(ROOT)
    licence = Path(os.environ.get(jira_real.LICENSE_VAR) or jira_real.LICENSE)
    if not licence.is_file():
        pytest.skip(f"no Jira test licence at {licence}: see docs/guides/jira-real.md")
    up = benchctl("up", "--cpu-nodes", "1", "--gpu-nodes", "0", "--with", "jira", "--with", "jira-real")
    assert up.returncode == 0, up.stdout[-600:] + up.stderr[-600:]
    setup = benchctl("setup", "jira-real")
    assert setup.returncode == 0, setup.stdout[-600:] + setup.stderr[-600:]
    yield jira_real.read_env(ROOT / jira_real.ENV)
    benchctl("remove", "jira-real-state", "-y")
    stack.down(say=lambda m: None)
    workbench.forget()


def test_both_jiras_answer_side_by_side(live):
    assert call(MOCK, "/rest/api/2/serverInfo")[0] == 200
    status, info = call(REAL, "/rest/api/2/serverInfo", token=live["PAT_ADMIN"])
    assert status == 200 and info["deploymentType"] == "Server" and info["version"].startswith("10.")


def test_the_seeded_users_project_and_tickets_exist(live):
    assert call(REAL, "/rest/api/2/myself", token=live["PAT_ALICE"])[1]["name"] == "alice"
    status, found = call(REAL, "/rest/api/2/search", {"jql": f"project = {jira_real.PROJECT}", "maxResults": 0}, token=live["PAT_ADMIN"])
    assert status == 200 and found["total"] >= jira_real.TICKETS
    names = {u["name"] for u in call(REAL, "/rest/api/2/user/search?username=.&maxResults=50", token=live["PAT_ADMIN"])[1]}
    assert {n for n, _ in jira_real.PEOPLE} <= names


def test_a_real_ticket_round_trip_create_comment_move_search(live):
    token = live["PAT_ALICE"]
    status, made = call(REAL, "/rest/api/2/issue", {"fields": {"project": {"key": jira_real.PROJECT}, "issuetype": {"name": "Task"},
                                                                "summary": "live round trip", "labels": ["workbench-live"]}}, token=token)
    assert status == 201, made
    key = made["key"]
    assert call(REAL, f"/rest/api/2/issue/{key}/comment", {"body": "from the live test"}, token=token)[0] == 201
    moves = call(REAL, f"/rest/api/2/issue/{key}/transitions", token=token)[1]["transitions"]
    assert moves, "Jira offers no transition: tools must read these, not assume names"
    assert call(REAL, f"/rest/api/2/issue/{key}/transitions", {"transition": {"id": moves[0]["id"]}}, token=token)[0] == 204
    assert wait_for(lambda: key in [i["key"] for i in call(REAL, "/rest/api/2/search?jql=" + urllib.parse.quote("labels = workbench-live"), token=token)[1]["issues"]], timeout=60)


def test_no_token_or_password_is_in_the_connections_row_or_the_services_listing(live):
    out = benchctl("get", "services", "-o", "json", timeout=120).stdout
    assert "jira-real" in out and not any(v in out for v in live.values() if len(v) > 12)


def test_the_ticket_driver_does_the_same_on_the_mock_and_the_real_jira(live):
    """One script, both Jiras: a fault opens one ticket, a repeat adds no ticket, recovery resolves it, the team moves tickets on."""
    from benchctl import ticketbot
    from benchctl.jira_driver import JiraDriver
    node = f"cn{os.getpid() % 900 + 100}"
    for target in ("mock", "real"):
        d = JiraDriver.for_target(target, root=ROOT)
        opened = d.fault(node, "power", "live test: powered off")["created"]
        assert d.fault(node, "power", "live test: powered off") == {"existing": opened}
        assert d.fault(node, "", "live test: back", open=False) == {"resolved": [opened]}
        status = d.call("GET", f"/issue/{opened}?fields=status")["fields"]["status"]
        assert (status.get("statusCategory") or {}).get("key") == "done" or status["name"] == "Resolved", (target, status)
        assert any("Recovered" in c for c in d.comments(opened))
        for i in d.search(f'labels = "node-{node}"'):
            d.move(i["key"], "Closed")
        assert ticketbot.team(d, every=0, count=3, say=lambda m: None, sleep=lambda s: None) == 3


def test_a_revoked_token_gets_a_real_401_and_a_new_one_works(live):
    """Issue 307: the scenario fault, not the library call: revoke, 401, restore, the new value in the env file works."""
    from benchctl import scenario
    old = live["PAT_ALICE"]
    assert call(REAL, "/rest/api/2/myself", token=old)[0] == 200
    assert scenario.execute(scenario.Step(0, "jira-real", {"fault": "token_revoke", "user": "alice"})).status == "ok"
    assert call(REAL, "/rest/api/2/myself", token=old)[0] == 401
    assert scenario.execute(scenario.Step(0, "jira-real", {"fault": "token_restore", "user": "alice"})).status == "ok"
    new = jira_real.read_env(ROOT / jira_real.ENV)["PAT_ALICE"]
    assert new != old and call(REAL, "/rest/api/2/myself", token=new)[0] == 200


def test_the_fault_proxy_fails_and_clears_real_calls(live):
    """Issue 305: 503 and 429 carry Retry-After, a slow answer is late but real, :8083 is never touched."""
    import time

    from benchctl import jira_proxy, scenario
    proxy = jira_proxy.shared()
    through = f"http://127.0.0.1:{proxy.port}"
    token = live["PAT_ADMIN"]
    try:
        assert scenario.execute(scenario.Step(0, "jira-real", {"fault": "proxy_503", "count": 1, "retry_after": 9})).status == "ok"
        assert call(REAL, "/rest/api/2/project", token=token)[0] == 200  # direct: untouched
        assert call(through, "/rest/api/2/project", token=token)[0] == 503
        assert call(through, "/rest/api/2/project", token=token)[0] == 200  # count used up
        scenario.execute(scenario.Step(0, "jira-real", {"fault": "proxy_slow", "count": 1, "delay": 3}))
        t = time.monotonic()
        assert call(through, "/rest/api/2/project", token=token)[0] == 200 and time.monotonic() - t >= 3
    finally:
        scenario.execute(scenario.Step(0, "jira-real", {"fault": "proxy_off"}))


def test_scenario_ticket_steps_file_and_move_real_tickets(live):
    """Issue 306: the ticket wave of client-stress-real, with its expectation, on the real Jira."""
    from benchctl import scenario
    jql = 'labels = steptest AND created >= -10m'
    made = {"tickets": {"action": "create", "count": 3, "summary": "Step test {n}", "labels": ["steptest"]}}
    assert scenario.execute(scenario.Step(0, "jira-real", made)).status == "ok"
    assert scenario.check_expect({"jira.issue_count": {"jql": jql, "count": 3, "target": "real", "within": "30s"}}, 1.0).status == "ok"
    moved = {"tickets": {"action": "transition", "jql": jql, "to": "In Progress"}}
    assert scenario.execute(scenario.Step(0, "jira-real", moved)).status == "ok"
    assert scenario.check_expect({"jira.issue_status": {"jql": jql, "status": "In Progress", "count": 3, "target": "real", "within": "30s"}}, 1.0).status == "ok"
