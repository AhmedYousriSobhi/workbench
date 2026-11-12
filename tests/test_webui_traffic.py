"""The merged request record for the Traffic analysis screen (issue 501): one answer from three mocks, honest about the ones it cannot read."""
from webui import traffic

ROW = {"who": "curl", "shape": "GET /x", "n": 3, "ok": 2, "client_error": 1, "server_error": 0, "ms": 9.0, "hist": [3, 0, 0, 0, 0, 0, 0, 0, 0]}


def fake(table):
    def fetch(url, body=None, timeout=2.0):
        for base, answer in table.items():
            if url.startswith(base):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise OSError("no such mock")
    return fetch


def answer(n, t0):
    return {"now": t0 + 10, "edges": [5, 10], "streams": [dict(ROW)], "recent": [[t0 + i, "curl", "GET /x", 200, 1.5] for i in range(n)]}


def test_one_answer_from_three_mocks_with_the_service_named_on_every_row():
    got = traffic.collect(0.0, fake({traffic.BMC: answer(2, 100.0), traffic.BCM: answer(1, 101.5), traffic.JIRA: answer(1, 99.0)}))
    assert got["services"] == {"bmc-farm": True, "bcm-mock": True, "jira-mock": True}
    assert sorted(s["service"] for s in got["streams"]) == ["bcm-mock", "bmc-farm", "jira-mock"]
    assert [r[0] for r in got["recent"]] == sorted(r[0] for r in got["recent"]) and {r[2] for r in got["recent"]} == {"bmc-farm", "bcm-mock", "jira-mock"}
    assert got["edges"] == [5, 10] and len(got["recent"][0]) == 6


def test_a_mock_that_is_down_or_too_old_is_said_to_be_so_not_counted_as_zero():
    got = traffic.collect(0.0, fake({traffic.BMC: OSError("down"), traffic.BCM: {"requests": 4, "errors": 0}, traffic.JIRA: answer(1, 5.0)}))
    assert got["services"] == {"bmc-farm": False, "bcm-mock": None, "jira-mock": True}
    assert [s["service"] for s in got["streams"]] == ["jira-mock"]


def test_since_is_passed_on_and_the_recent_list_is_capped():
    seen = []

    def fetch(url, body=None, timeout=2.0):
        seen.append(url)
        return answer(traffic.RECENT_CAP, 1000.0)
    got = traffic.collect(42.5, fetch)
    assert all(u.endswith("?since=42.500") for u in seen) and len(got["recent"]) == traffic.RECENT_CAP


def test_the_answer_carries_no_header_value_or_body():
    import json
    got = json.dumps(traffic.collect(0.0, fake({traffic.BMC: answer(1, 1.0), traffic.BCM: answer(0, 1.0), traffic.JIRA: answer(0, 1.0)})))
    assert "Authorization" not in got and "Bearer" not in got


def test_the_diagrams_get_the_slurm_facts_on_every_visible_tab():
    """Issue 567: slurmctld 'not probed' and a missing accounting/monitoring lane until the Traffic tab had been opened once."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "webui/static/js/traffic.js").read_text()
    body = src.split("export async function paintSlurmStats()")[1].split("\n}\n")[0]
    assert "tab-traffic" not in body.split("S.slurmAt = Date.now()")[0]  # the gate is the page being visible, not the Traffic tab
    assert "document.hidden" in body and body.count("paintBlocks()") == 2  # the diagrams repaint when the facts arrive


def test_the_zoom_bar_on_home_opens_the_diagram_instead_of_doing_nothing():
    """Issue 567 follow-up: Home has no diagram, yet the bar showed Cluster pressed and a click changed nothing."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "webui/static/js/zoom.js").read_text()
    assert "const on = !none && b.dataset.level === lv" in src  # no level is marked on Home or Test run
    assert "offInfra() && !onConn()" in src.split("export function zoomTo")[1]  # a click there selects the Infrastructure tab at that level
