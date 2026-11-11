"""Who asked, how it ended and how long it took (issue 501) against the live mocks and the page's own /api/traffic."""
import json
import urllib.error
import urllib.request

from test_cluster_real import api, ui  # noqa: F401  (the module-scoped live stack and UI process)

from webui.flow import STATS

JIRA = "http://127.0.0.1:8080"


def get(url, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except urllib.error.HTTPError:
        pass


def test_the_live_mocks_record_the_caller_the_status_class_and_a_latency_and_the_page_merges_them(ui):  # noqa: F811
    base, h, _token = ui
    for _ in range(3):
        get(f"{JIRA}/rest/api/2/search?jql=project%3DOPS", "WorkbenchProbeTest/1.2.3 (secret-detail)")
    get(f"{JIRA}/rest/api/2/no-such-thing", "WorkbenchProbeTest/1.2.3")  # a 404 on a path the mock does not serve
    direct = json.loads(urllib.request.urlopen(f"{JIRA}/sim/traffic", timeout=5).read())
    mine = [s for s in direct["streams"] if s["who"] == "workbenchprobetest"]
    assert sum(s["n"] for s in mine) == 4 and sum(s["client_error"] for s in mine) == 1
    assert all(len(s["hist"]) == len(direct["edges"]) + 1 and s["ms"] >= 0 for s in mine)
    assert "secret-detail" not in json.dumps(direct) and "1.2.3" not in json.dumps(direct)  # the product token only
    merged = api(base, h, "/api/traffic")
    assert merged["services"]["jira-mock"] is True and set(merged["services"]) == set(STATS) - {"gpu-exporter"}
    assert any(s["who"] == "workbenchprobetest" and s["service"] == "jira-mock" for s in merged["streams"])
    assert all(r[2] in merged["services"] for r in merged["recent"])
    # asking for the record is not client traffic: the totals do not move because of it
    before = json.loads(urllib.request.urlopen(f"{JIRA}/sim/stats", timeout=5).read())["requests"]
    api(base, h, "/api/traffic")
    assert json.loads(urllib.request.urlopen(f"{JIRA}/sim/stats", timeout=5).read())["requests"] == before
