"""The ticket driver against a Data Center shaped Jira (issue 284): another workflow (To Do / In Progress / Done), no search/jql,
no Incident type, users by name, no ops-bot. The same calls the real Jira gets; the mock covers the other workflow."""
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from benchctl import jira_driver as jd

STATUS = {"To Do": ("10000", "new"), "In Progress": ("10001", "indeterminate"), "Done": ("10002", "done")}
USERS = {"alice", "bob", "carol", "dave", "erin", "admin"}


class Fake(BaseHTTPRequestHandler):
    issues: ClassVar[dict] = {}
    seen: ClassVar[list] = []

    def log_message(self, *a):
        pass

    def reply(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def view(self, i):
        name = i["status"]
        return {**i, "status": {"name": name, "statusCategory": {"key": STATUS[name][1]}}}

    def route(self, method):
        cls, path, b = type(self), self.path.split("?")[0], self.body() if method != "GET" else {}
        cls.seen.append((method, path))
        parts = path.removeprefix("/rest/api/2/").split("/")
        if self.headers.get("Authorization") != "Bearer tok":
            return self.reply(401, {})
        if path.endswith("/search/jql"):
            return self.reply(404, {"errorMessages": ["no such endpoint on Data Center"]})
        if parts == ["search"]:
            assert "statusCategory != Done" in b["jql"] or "workbench" in b["jql"] or "node-" in b["jql"]
            hit = [i for i in cls.issues.values() if ("statusCategory != Done" not in b["jql"] or STATUS[i["status"]][1] != "done")]
            for lab in [w.split('"')[1] for w in b["jql"].split("labels = ")[1:]]:
                hit = [i for i in hit if lab in i["labels"]]
            page = hit[b.get("startAt", 0):][:b["maxResults"]]
            return self.reply(200, {"total": len(hit), "issues": [{"key": i["key"], "fields": self.view(i)} for i in page]})
        if parts == ["project", "OPS"]:
            return self.reply(200, {"issueTypes": [{"name": n} for n in ("Epic", "Task", "Bug", "Subtask")]})
        if parts == ["issue"] and method == "POST":
            f = b["fields"]
            assert f["issuetype"]["name"] in ("Task", "Bug"), f["issuetype"]
            assert "reporter" not in f or f["reporter"]["name"] in USERS
            key = f"OPS-{len(cls.issues) + 1}"
            cls.issues[key] = {"key": key, "summary": f["summary"], "labels": f["labels"], "status": "To Do", "comments": [],
                               "updated": "2020-01-01T00:00:00.000+0000", "assignee": None, "priority": f["priority"]["name"]}
            return self.reply(201, {"key": key})
        issue = cls.issues.get(parts[1]) if len(parts) > 1 else None
        if issue and parts[2:] == ["comment"]:
            if method == "POST":
                issue["comments"].append(b["body"])
                return self.reply(201, {})
            return self.reply(200, {"comments": [{"body": c} for c in issue["comments"]]})
        if issue and parts[2:] == ["transitions"]:
            if method == "GET":
                return self.reply(200, {"transitions": [{"id": STATUS[n][0], "name": n, "to": {"name": n, "statusCategory": {"key": STATUS[n][1]}}}
                                                        for n in STATUS if n != issue["status"]]})
            issue["status"] = next(n for n, (i, _) in STATUS.items() if i == b["transition"]["id"])
            return self.reply(204)
        if issue and parts[2:] == ["assignee"]:
            assert b["name"] in USERS
            issue["assignee"] = b["name"]
            return self.reply(204)
        if issue and method == "GET":
            return self.reply(200, {"key": issue["key"], "fields": self.view(issue)})
        return self.reply(404, {})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")


@pytest.fixture
def dc():
    Fake.issues, Fake.seen = {}, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield jd.JiraDriver(f"http://127.0.0.1:{srv.server_port}", auth="Bearer tok", dc=True, rng=random.Random(2))
    srv.shutdown()


def test_a_fault_opens_one_ticket_a_repeat_comments_and_recovery_resolves_it(dc):
    assert "created" in dc.fault("cn003", "power", "powered off")
    assert "existing" in dc.fault("cn003", "power", "powered off")
    dc.fault("cn003", "slurm", "Slurm state down", merge=True)
    assert len(Fake.issues) == 1 and len(Fake.issues["OPS-1"]["comments"]) == 2
    assert dc.fault("cn003", "", "back", open=False) == {"resolved": ["OPS-1"]}
    assert Fake.issues["OPS-1"]["status"] == "Done"
    assert "created" in dc.fault("cn003", "nic", "flapping") and len(Fake.issues) == 2


def test_it_never_uses_search_jql_and_files_a_bug_for_the_missing_incident_type(dc):
    dc.fault("cn001", "fan", "fan 2")
    assert ("POST", "/rest/api/2/search/jql") not in Fake.seen and ("POST", "/rest/api/2/search") in Fake.seen
    assert "workbench" in Fake.issues["OPS-1"]["labels"]


def test_move_reaches_resolved_and_closed_in_a_workflow_that_only_has_done(dc):
    key = dc.create("walk")
    dc.move(key, "Resolved")
    assert Fake.issues[key]["status"] == "Done"
    dc.move(key, "Closed")  # nothing to do: Done is the end
    assert Fake.issues[key]["status"] == "Done"
    dc.move(key, "Open")
    assert Fake.issues[key]["status"] == "To Do"
    dc.move(key, "In Progress")
    assert Fake.issues[key]["status"] == "In Progress"


def test_tick_moves_assigns_and_comments_with_real_names(dc):
    for n in range(8):
        dc.create(f"t{n}")
    done = {r.split()[0] for r in (dc.tick() for _ in range(60)) if r}
    assert {"advance", "comment", "assign"} <= done
    assert {i["assignee"] for i in Fake.issues.values()} - {None} <= set(jd.ENGINEERS)
    assert {i["status"] for i in Fake.issues.values()} - {"To Do"}


def test_for_target_mock_needs_nothing_and_real_needs_setup(tmp_path):
    assert jd.JiraDriver.for_target("mock", environ={}).base == "http://127.0.0.1:8080"
    with pytest.raises(jd.JiraError, match="setup jira-real"):
        jd.JiraDriver.for_target("real", root=tmp_path)
    env = tmp_path / "compose/generated/secrets"
    env.mkdir(parents=True)
    (env / "jira-real.env").write_text("JIRA_URL=http://127.0.0.1:8083\nPAT_ADMIN=abc\n")
    d = jd.JiraDriver.for_target("real", root=tmp_path)
    assert d.dc and d.auth == "Bearer abc" and d.base.endswith(":8083")
    with pytest.raises(jd.JiraError):
        jd.JiraDriver.for_target("other")


def test_a_done_ticket_is_never_advanced_again_and_the_cooldown_counts_the_time_zone(dc):
    key = dc.create("finished")
    dc.move(key, "Done")
    Fake.issues[key]["updated"] = "2020-01-01T00:00:00.000+0000"
    for _ in range(40):
        was = Fake.issues[key]["status"]
        r = dc.tick()
        assert not (was == "Done" and r == f"advance {key}"), "Done is the end of the workflow"
    assert jd._epoch("2026-11-05T08:40:04.123+0200") == jd._epoch("2026-11-05T06:40:04.000+0000")
    assert jd._epoch("2026-11-05T08:40:04.123-0500") == jd._epoch("2026-11-05T13:40:04.000+0000")
    assert jd._epoch("2026-11-05T08:40:04") == jd._epoch("2026-11-05T08:40:04.000Z")
