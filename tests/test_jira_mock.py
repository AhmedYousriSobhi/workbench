import importlib.util
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("jira", ROOT / "components/jira-mock/jira.py")
jira = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jira)


@pytest.fixture
def server():
    delivered, slept = [], []
    j = jira.Jira(deliver=lambda url, payload: delivered.append((url, payload)), sleep=slept.append)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), jira.make_handler(j))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", delivered, slept
    srv.shutdown()


def call(base, path, body=None, method=None):
    req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                  method=method or ("GET" if body is None else "POST"))
    try:
        with urlopen(req, timeout=5) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None, dict(r.headers)
    except HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


def new(base, summary="GPU down", project="OPS", **fields):
    return call(base, "/rest/api/2/issue", {"fields": {"project": {"key": project}, "summary": summary, **fields}})


def test_create_get_and_numbering(server):
    base, *_ = server
    assert new(base)[1]["key"] == "OPS-1"
    assert new(base)[1]["key"] == "OPS-2"
    assert new(base, project="INFRA")[1]["key"] == "INFRA-1"
    code, issue, _ = call(base, "/rest/api/3/issue/OPS-1")  # v3 alias
    assert code == 200 and issue["fields"]["status"]["name"] == "Open"
    assert call(base, "/rest/api/2/issue/OPS-99")[0] == 404


def test_create_validates_fields(server):
    base, *_ = server
    code, body, _ = call(base, "/rest/api/2/issue", {"fields": {}})
    assert code == 400 and set(body["errors"]) == {"project", "summary"}


def test_resolve_requires_resolution_per_field(server):
    base, *_ = server
    new(base)
    code, body, _ = call(base, "/rest/api/2/issue/OPS-1/transitions", {"transition": {"id": "31"}})
    assert code == 400 and body["errors"] == {"resolution": "Resolution is required."}
    assert call(base, "/rest/api/2/issue/OPS-1")[1]["fields"]["status"]["name"] == "Open"  # unchanged
    ok = call(base, "/rest/api/2/issue/OPS-1/transitions",
              {"transition": {"id": "31"}, "fields": {"resolution": {"name": "Fixed"}}})
    assert ok[0] == 204
    names = [t["name"] for t in call(base, "/rest/api/2/issue/OPS-1/transitions")[1]["transitions"]]
    assert names == ["Close", "Reopen"]
    assert call(base, "/rest/api/2/issue/OPS-1/transitions", {"transition": {"id": "11"}})[0] == 400  # not valid now


def test_comment_and_empty_comment(server):
    base, *_ = server
    new(base)
    assert call(base, "/rest/api/2/issue/OPS-1/comment", {"body": "looking"})[0] == 201
    assert call(base, "/rest/api/2/issue/OPS-1/comment", {"body": ""})[0] == 400
    assert len(call(base, "/rest/api/2/issue/OPS-1")[1]["fields"]["comment"]["comments"]) == 1


def test_jql_subset(server):
    base, *_ = server
    new(base, labels=["gpu"])
    new(base, summary="b", labels=["net"])
    new(base, summary="c", project="INFRA")
    call(base, "/rest/api/2/issue/OPS-2/transitions", {"transition": {"id": "11"}})

    def q(jql):
        return [i["key"] for i in call(base, "/rest/api/2/search?jql=" + quote(jql))[1]["issues"]]

    assert q("project = OPS ORDER BY created DESC") == ["OPS-1", "OPS-2"]
    assert q('project = OPS AND status = "In Progress"') == ["OPS-2"]
    assert q("labels = gpu") == ["OPS-1"]
    assert q("status in (Open, Closed) AND project != INFRA") == ["OPS-1"]
    code, body, _ = call(base, "/rest/api/2/search?jql=" + quote("summary ~ x"))
    assert code == 400 and "JQL" in body["errorMessages"][0]


def test_webhooks_fire_on_create_comment_and_transition(server):
    base, delivered, _ = server
    assert call(base, "/rest/webhooks/1.0/webhook", {"url": "http://receiver/hook"})[0] == 201
    new(base)
    call(base, "/rest/api/2/issue/OPS-1/comment", {"body": "x"})
    call(base, "/rest/api/2/issue/OPS-1/transitions", {"transition": {"id": "11"}})
    deadline = time.time() + 3
    while len(delivered) < 3 and time.time() < deadline:
        time.sleep(0.02)
    events = sorted(p["webhookEvent"] for _, p in delivered)
    assert events == ["comment_created", "jira:issue_created", "jira:issue_updated"]
    assert {u for u, _ in delivered} == {"http://receiver/hook"}


def test_failure_modes_count_down(server):
    base, _, slept = server
    assert call(base, "/sim/failure", {"mode": "429", "count": 2})[0] == 200
    code, _, headers = new(base)
    assert code == 429 and headers["Retry-After"] == "1"
    assert new(base)[0] == 429
    assert new(base)[0] == 201  # budget spent, normal again
    call(base, "/sim/failure", {"mode": "503", "count": 1})
    assert new(base)[0] == 503
    call(base, "/sim/failure", {"mode": "slow", "count": 1, "delay": 4})
    assert new(base)[0] == 201 and slept == [4.0]
    assert call(base, "/sim/failure", {"mode": "bogus"})[0] == 400
    assert call(base, "/healthz")[0] == 200  # health is never failed
