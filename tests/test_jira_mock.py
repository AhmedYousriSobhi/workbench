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

    assert q("project = OPS ORDER BY created DESC") == ["OPS-2", "OPS-1"]  # newest first, as in Jira
    assert q("project = OPS ORDER BY created ASC") == ["OPS-1", "OPS-2"]
    assert q('project = OPS AND status = "In Progress"') == ["OPS-2"]
    assert q("labels = gpu") == ["OPS-1"]
    assert q("status in (Open, Closed) AND project != INFRA") == ["OPS-1"]
    assert sorted(q("watcher = currentUser()")) == ["INFRA-1", "OPS-1", "OPS-2"]  # the reporter is a watcher
    assert q("watcher = nobody") == []
    assert q('updated >= "2000/01/01 00:00"') != [] and q('updated >= "2999/01/01 00:00"') == []  # slashes, as a client writes them
    code, body, _ = call(base, "/rest/api/2/search?jql=" + quote("nosuchfield = x"))
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


# --- the calls a monitoring client makes: POST search (v3 token paging, v2 offset paging), /myself, /field, /serverInfo ---

FIELDS = ["summary", "status", "issuetype", "updated", "assignee"]


def test_v3_search_jql_post_pages_by_token_without_a_total(server):
    base, *_ = server
    for n in range(5):
        new(base, summary=f"t{n}", labels=["gpu-farm"])
    seen, token = [], None
    while True:
        body = {"jql": '(project = "OPS" AND (labels in ("gpu-farm") OR text ~ "zzz")) ORDER BY created DESC',
                "maxResults": 2, "fields": FIELDS, **({"nextPageToken": token} if token else {})}
        code, page, _ = call(base, "/rest/api/3/search/jql", body)
        assert code == 200 and "total" not in page
        seen += [i["key"] for i in page["issues"]]
        assert set(page["issues"][0]["fields"]) <= set(FIELDS)  # only what was asked for
        token = page.get("nextPageToken")
        if page["isLast"]:
            break
    assert seen == ["OPS-5", "OPS-4", "OPS-3", "OPS-2", "OPS-1"] and token is None


def test_v2_search_post_pages_by_offset_with_a_total(server):
    base, *_ = server
    for n in range(3):
        new(base, summary=f"t{n}")
    code, page, _ = call(base, "/rest/api/2/search", {"jql": "project = OPS", "maxResults": 2, "startAt": 2, "fields": FIELDS})
    assert code == 200 and page["total"] == 3 and [i["key"] for i in page["issues"]] == ["OPS-3"]


def test_issue_fields_a_ticket_list_needs(server):
    base, *_ = server
    new(base, assignee={"name": "bob"})
    f = call(base, "/rest/api/2/issue/OPS-1")[1]["fields"]
    assert f["status"]["statusCategory"]["key"] == "new" and f["assignee"]["displayName"] == "Bob Example"
    assert f["reporter"]["name"] == "alice" and f["priority"]["name"] == "Medium"
    call(base, "/rest/api/2/issue/OPS-1/transitions", {"transition": {"id": "31"}, "fields": {"resolution": "Fixed"}})
    done = call(base, "/rest/api/2/search?jql=" + quote("statusCategory = Done"))[1]["issues"]
    assert [i["key"] for i in done] == ["OPS-1"]


def test_myself_follows_the_auth_style(server):
    import base64
    base, *_ = server
    assert call(base, "/rest/api/2/myself")[1]["name"] == "alice"
    req = Request(base + "/rest/api/3/myself", headers={"Authorization": "Basic " + base64.b64encode(b"carol@example.org:tok").decode()})
    me = json.loads(urlopen(req, timeout=5).read())
    assert me["emailAddress"] == "carol@example.org" and me["accountId"] == "carol-id"
    req = Request(base + "/rest/api/2/myself", headers={"Authorization": "Basic !!!"})
    assert json.loads(urlopen(req, timeout=5).read())["name"] == "alice"


def test_server_info_fields_and_projects(server):
    base, *_ = server
    info = call(base, "/rest/api/2/serverInfo")[1]
    assert info["version"] and info["deploymentType"] == "Server"
    assert {"summary", "status", "assignee"} <= {f["id"] for f in call(base, "/rest/api/3/field")[1]}
    assert call(base, "/rest/api/2/project")[1][0]["key"] == "OPS"  # a default before any issue exists
    new(base, project="INFRA")
    assert [p["key"] for p in call(base, "/rest/api/3/project/search")[1]["values"]] == ["INFRA"]


def test_401_failure_mode_and_bad_search_numbers(server):
    base, *_ = server
    assert call(base, "/sim/failure", {"mode": "401", "count": 1})[0] == 200
    code, body, _ = call(base, "/rest/api/2/myself")
    assert code == 401 and "not authenticated" in body["errorMessages"][0]
    assert call(base, "/rest/api/2/myself")[0] == 200  # count spent
    assert call(base, "/rest/api/3/search/jql", {"jql": "project = OPS", "maxResults": "x"})[0] == 400
    assert call(base, "/rest/api/3/search/jql?jql=" + quote("project = OPS") + "&fields=summary,status")[0] == 200


def test_edit_an_issue_and_its_labels_and_assignee(server):
    base, delivered, _ = server
    key = new(base, labels=["a"])[1]["key"]
    call(base, "/rest/webhooks/1.0/webhook", {"url": "http://hook", "events": ["jira:issue_updated"]})
    code, _, _ = call(base, f"/rest/api/2/issue/{key}", {"fields": {"summary": "New", "priority": {"name": "High"},
                      "description": "d"}, "update": {"labels": [{"add": "b"}, {"remove": "a"}]}}, "PUT")
    assert code == 204
    f = call(base, f"/rest/api/2/issue/{key}")[1]["fields"]
    assert (f["summary"], f["priority"]["name"], f["description"], f["labels"]) == ("New", "High", "d", ["b"])
    assert call(base, f"/rest/api/2/issue/{key}", {"fields": {"summary": ""}}, "PUT")[0] == 400
    assert call(base, f"/rest/api/2/issue/{key}/assignee", {"name": "bob"}, "PUT")[0] == 204
    assert call(base, f"/rest/api/2/issue/{key}")[1]["fields"]["assignee"]["name"] == "bob"
    assert call(base, f"/rest/api/2/issue/{key}/assignee", None, "PUT")[0] == 204
    assert call(base, f"/rest/api/2/issue/{key}")[1]["fields"]["assignee"] is None
    time.sleep(0.2)
    assert delivered and delivered[0][1]["webhookEvent"] == "jira:issue_updated"


def test_comments_can_be_read_edited_and_deleted(server):
    base, *_ = server
    key = new(base)[1]["key"]
    a = call(base, f"/rest/api/2/issue/{key}/comment", {"body": "one", "visibility": {"type": "role", "value": "Administrators"}})[1]
    b = call(base, f"/rest/api/2/issue/{key}/comment", {"body": "two"})[1]
    assert (a["id"], b["id"]) == ("1", "2") and a["author"]["name"] == "alice" and a["visibility"]["type"] == "role"
    assert call(base, f"/rest/api/2/issue/{key}/comment")[1]["total"] == 2
    assert call(base, f"/rest/api/2/issue/{key}/comment/1", {"body": "edited"}, "PUT")[1]["body"] == "edited"
    assert call(base, f"/rest/api/2/issue/{key}/comment/1", {"body": ""}, "PUT")[0] == 400
    assert call(base, f"/rest/api/2/issue/{key}/comment/1", None, "DELETE")[0] == 204
    assert call(base, f"/rest/api/2/issue/{key}/comment/1", None, "DELETE")[0] == 404
    assert call(base, f"/rest/api/2/issue/{key}/comment", {"body": "three"})[1]["id"] == "3"  # ids never repeat


def test_watchers_and_worklog_and_user_search(server):
    base, *_ = server
    key = new(base)[1]["key"]
    assert call(base, f"/rest/api/2/issue/{key}/watchers", "alice")[0] == 204
    w = call(base, f"/rest/api/2/issue/{key}/watchers")[1]
    assert w["watchCount"] == 1 and w["isWatching"] is True
    assert call(base, f"/rest/api/2/issue/{key}")[1]["fields"]["watches"]["watchCount"] == 1
    assert call(base, f"/rest/api/2/issue/{key}/watchers?username=alice", None, "DELETE")[0] == 204
    assert call(base, f"/rest/api/2/issue/{key}/watchers")[1]["watchCount"] == 0
    log = call(base, f"/rest/api/2/issue/{key}/worklog", {"timeSpent": "1h 30m", "comment": "ssh"})
    assert log[0] == 201 and log[1]["timeSpentSeconds"] == 5400
    assert call(base, f"/rest/api/2/issue/{key}/worklog", {"timeSpent": "soon"})[0] == 400
    assert call(base, f"/rest/api/2/issue/{key}/worklog")[1]["total"] == 1
    assert call(base, f"/rest/api/2/issue/{key}")[1]["fields"]["timespent"] == 5400
    names = {u["name"] for u in call(base, "/rest/api/2/user/search?query=")[1]}
    assert {"alice", "bob"} <= names
    assert [u["name"] for u in call(base, "/rest/api/2/user/assignable/search?username=bo&issueKey=" + key)[1]] == ["bob"]


def test_sim_tickets_creates_moves_comments_and_resets(server):
    base, *_ = server
    made = call(base, "/sim/tickets", {"action": "create", "count": 3, "summary": "Job {n} failed", "labels": ["wave"],
                                        "status": "Resolved", "priority": "High"})[1]["created"]
    assert made == ["OPS-1", "OPS-2", "OPS-3"]
    f = call(base, "/rest/api/2/issue/OPS-2")[1]["fields"]
    assert f["summary"] == "Job 2 failed" and f["status"]["name"] == "Resolved" and f["priority"]["name"] == "High"
    assert f["resolution"] == {"name": "Done"}
    assert call(base, "/rest/api/2/search?jql=" + quote("resolution = Done"))[1]["total"] == 3
    new(base, labels=["x"])
    moved = call(base, "/sim/tickets", {"action": "transition", "jql": "labels = x", "to": "Closed"})[1]["matched"]
    assert moved == ["OPS-4"] and call(base, "/rest/api/2/issue/OPS-4")[1]["fields"]["status"]["name"] == "Closed"
    call(base, "/sim/tickets", {"action": "comment", "jql": "labels = x", "body": "bye"})
    assert call(base, "/rest/api/2/issue/OPS-4")[1]["fields"]["comment"]["comments"][0]["body"] == "bye"
    over = call(base, "/sim/tickets")[1]
    assert over["total"] == 4 and over["by_status"] == {"Resolved": 3, "Closed": 1}
    assert call(base, "/sim/tickets", {"action": "reset"})[1] == {"reset": True}
    assert call(base, "/sim/tickets")[1]["total"] == 0
    assert new(base)[1]["key"] == "OPS-1"  # numbering starts again


def test_sim_tickets_rejects_bad_requests(server):
    base, *_ = server
    assert call(base, "/sim/tickets", {"action": "nope"})[0] == 400
    new(base)
    assert call(base, "/sim/tickets", {"action": "transition", "jql": "project = OPS", "to": "Done"})[0] == 400
    assert call(base, "/sim/tickets", {"action": "create"})[0] == 400  # no summary


def test_sim_tickets_are_not_counted_as_client_requests(server):
    base, *_ = server
    call(base, "/sim/tickets", {"action": "create", "summary": "x"})
    assert call(base, "/sim/stats")[1]["requests"] == 0


def test_workflow_route_finds_the_shortest_path():
    assert jira.route("Open", "Closed") == [("31", ["resolution"]), ("41", [])]
    assert jira.route("Closed", "Resolved") == [("51", []), ("31", ["resolution"])]
    assert jira.route("Open", "Open") == []
    assert jira.duration_seconds("1w 2d") == 144000 + 57600 and jira.duration_seconds("1x") == 0


def test_forms_get_field_screens_and_resolution_by_id(server):
    """A client draws its Resolve and Edit forms from these (#109): schema and allowed values, and a resolution sent as an id."""
    base, *_ = server
    new(base)
    moves = call(base, "/rest/api/2/issue/OPS-1/transitions?expand=transitions.fields")[1]["transitions"]
    res = next(t for t in moves if t["name"] == "Resolve")["fields"]["resolution"]
    assert res["required"] and res["schema"]["type"] == "resolution" and {"id": "10000", "name": "Done"} in res["allowedValues"]
    meta = call(base, "/rest/api/2/issue/OPS-1/editmeta")[1]["fields"]
    assert set(meta) == {"summary", "priority", "labels", "description"} and meta["priority"]["allowedValues"][0]["name"] == "Highest"
    assert call(base, "/rest/api/2/issue/NOPE-1/editmeta")[0] == 404
    code, _, _ = call(base, "/rest/api/2/issue/OPS-1/transitions", {"transition": {"id": "31"}, "fields": {"resolution": {"id": "10001"}}})
    assert code == 204 and call(base, "/rest/api/2/issue/OPS-1")[1]["fields"]["resolution"] == {"name": "Won't Do"}
    call(base, "/rest/api/2/issue/OPS-1", {"fields": {"priority": {"id": "1"}}}, method="PUT")
    assert call(base, "/rest/api/2/issue/OPS-1")[1]["fields"]["priority"]["name"] == "Highest"


def test_a_fault_opens_one_ticket_per_node_and_kind_and_recovery_resolves_it(server):
    base, *_ = server
    fault = lambda **b: call(base, "/sim/tickets", {"action": "fault", "node": "cn002", **b})
    key = fault(kind="power", detail="BMC power Off")[1]["created"]
    f = call(base, f"/rest/api/2/issue/{key}")[1]["fields"]
    assert f["summary"] == "cn002: node is down (powered off)" and f["priority"]["name"] == "Highest"
    assert {"node-cn002", "fault-power", "auto"} <= set(f["labels"]) and f["status"]["name"] == "Open"
    assert fault(kind="power", detail="BMC power Off")[1] == {"existing": key}
    assert fault(kind="power", detail="BMC power Off")[1] == {"existing": key}
    assert [c["body"] for c in call(base, f"/rest/api/2/issue/{key}")[1]["fields"]["comment"]["comments"]] == ["Still failing. BMC power Off"]
    assert fault(kind="xid", detail="XID 79")[1]["created"] != key  # another kind is another ticket
    assert call(base, "/sim/tickets", {"action": "fault", "node": "cn003", "kind": "power"})[1]["created"] != key  # another node too
    done = fault(open=False, kind="power")[1]["resolved"]
    assert done == [key] and call(base, f"/rest/api/2/issue/{key}")[1]["fields"]["status"]["name"] == "Resolved"
    assert fault(kind="power")[1]["created"] != key  # it failed again after it was resolved: a new ticket
    assert len(fault(open=False)[1]["resolved"]) == 2  # no kind: everything open for the node


def test_a_fault_needs_a_node_and_a_known_kind(server):
    base, *_ = server
    assert call(base, "/sim/tickets", {"action": "fault", "node": "cn001", "kind": "nope"})[0] == 400
    assert call(base, "/sim/tickets", {"action": "fault", "kind": "power"})[0] == 400
