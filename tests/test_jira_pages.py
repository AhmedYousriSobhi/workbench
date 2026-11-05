"""The Jira mock's Confluence page search (issue 289): the call the monitoring client's Confluence section makes."""
import importlib.util
import json
import threading
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
def mock():
    j = jira.Jira(deliver=lambda *a: None, sleep=lambda s: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), jira.make_handler(j))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield j, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def get(base, path):
    with urlopen(Request(base + path), timeout=5) as r:
        return json.loads(r.read())


def search(base, cql, cloud=False, **extra):
    q = "&".join([f"cql={quote(cql)}", "excerpt=none", *(f"{k}={v}" for k, v in extra.items())])
    return get(base, f"{'/wiki' if cloud else ''}/rest/api/search?{q}")


def post(base, path, body):
    with urlopen(Request(base + path, data=json.dumps(body).encode(), method="POST"), timeout=5) as r:
        return json.loads(r.read())


def test_a_client_style_query_finds_pages_by_label_or_title_on_both_paths(mock):
    _, base = mock
    cql = 'type = page AND (label in ("runbook") OR title ~ "overview") ORDER BY lastmodified DESC'
    for cloud in (False, True):
        out = search(base, cql, cloud, limit=25, start=0)
        titles = [r["title"] for r in out["results"]]
        assert "Runbook: node is down" in titles and "workbench cluster overview" in titles
        assert out["_links"]["base"].endswith("/wiki" if cloud else "127.0.0.1:" + base.rsplit(":", 1)[1])
        first = out["results"][0]
        assert first["url"] and first["resultGlobalContainer"]["title"] and first["content"]["space"]["name"] and first["lastModified"]


def test_the_text_filter_and_the_wide_form_the_client_adds(mock):
    _, base = mock
    only = search(base, 'type = page AND (label in ("runbook")) AND (title ~ "GPU" OR text ~ "GPU")')["results"]
    assert [r["title"] for r in only] == ["GPU error (XID) triage"]
    wide = search(base, 'type = page AND (title ~ "alice" OR text ~ "alice")')["results"]
    assert [r["title"] for r in wide] == ["workbench cluster overview"]  # only in the body
    assert search(base, 'type = page AND label in ("nothing")')["results"] == []


def test_paging_follows_start_and_limit_and_gives_a_next_link(mock):
    _, base = mock
    cql = 'type = page AND label in ("workbench")'
    first = search(base, cql, limit=3, start=0)
    assert first["size"] == 3 and first["totalSize"] == len(jira.PAGES) and "next" in first["_links"]
    last = search(base, cql, limit=3, start=6)
    assert last["size"] == 1 and "next" not in last["_links"]
    seen = {r["title"] for s in (0, 3, 6) for r in search(base, cql, limit=3, start=s)["results"]}
    assert len(seen) == len(jira.PAGES)


def test_sim_pages_adds_clears_and_resets(mock):
    _, base = mock
    post(base, "/sim/pages", {"action": "add", "title": "cn042 maintenance", "labels": ["cluster-a"], "body": "kernel update"})
    assert [r["title"] for r in search(base, 'type = page AND label in ("cluster-a")')["results"]] == ["cn042 maintenance"]
    assert post(base, "/sim/pages", {"action": "clear"}) == {"pages": 0}
    assert search(base, "type = page")["results"] == []
    assert post(base, "/sim/pages", {"action": "reset"})["pages"] == len(jira.PAGES)
    with pytest.raises(HTTPError) as e:
        post(base, "/sim/pages", {"action": "add"})
    assert e.value.code == 400


def test_a_bad_cql_is_a_400_with_a_message_and_failure_modes_apply(mock):
    _, base = mock
    with pytest.raises(HTTPError) as e:
        search(base, "colour = red")
    assert e.value.code == 400 and "cql" in e.value.read().decode()
    post(base, "/sim/failure", {"mode": "503", "count": 1})
    with pytest.raises(HTTPError) as e:
        search(base, "type = page")
    assert e.value.code == 503
    assert search(base, "type = page")["totalSize"] == len(jira.PAGES)


def test_jql_still_works_after_the_query_class_was_shared(mock):
    _, base = mock
    out = get(base, "/rest/api/2/search?jql=" + quote("project = OPS ORDER BY created"))
    assert "issues" in out and out["total"] >= 0
