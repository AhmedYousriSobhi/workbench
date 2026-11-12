"""Per-path counters and the unknown-path list on the mocks (issue 475): which surfaces a client really calls."""
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = {"bmc": "components/bmc-farm/bmc.py", "bcm": "components/bcm-mock/bcm.py", "jira": "components/jira-mock/jira.py"}


def load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_shapes", ROOT / FILES[name])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(), headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def block(path):
    text = (ROOT / path).read_text()
    return text[text.index("# --- request shapes"):text.index("SHAPES = Shapes()")]


def test_the_three_mocks_carry_the_same_shape_code():
    blocks = {block(p) for p in FILES.values()}
    assert len(blocks) == 1


def test_a_shape_hides_ids_and_query_values():
    m = load("jira")
    assert m.shape("/rest/api/2/issue/OPS-12/comment") == "/rest/api/2/issue/{id}/comment"
    assert m.shape("/rest/api/2/search?jql=assignee%3Dalice&maxResults=5") == "/rest/api/2/search?jql&maxResults"
    assert m.shape("/cn001/redfish/v1/Systems/1") == "/{id}/redfish/v1/Systems/1"
    assert "alice" not in m.shape("/x?token=secret-value")


def test_the_cap_folds_the_rest_into_other():
    m = load("jira")
    s = m.Shapes()
    for i in range(m.SHAPE_CAP + 5):
        s.add("GET", f"/p/seg{'a' * (i % 26)}{chr(97 + i // 26)}x", 200)
    assert len(s.paths) <= m.SHAPE_CAP + 1


def test_jira_counts_paths_and_lists_the_unknown_ones():
    m = load("jira")
    srv, base = serve(m.make_handler(m.Jira(deliver=lambda *a: None)))
    m.SHAPES.reset()
    hdr = {"Authorization": "Bearer x"}
    call(base + "/rest/api/2/serverInfo", headers=hdr)
    call(base + "/rest/api/2/serverInfo", headers=hdr)
    call(base + "/rest/api/2/nothing-here?secret=abc", headers=hdr)
    call(base + "/rest/api/2/serverInfo", headers={"X-Workbench-Probe": "1"})  # the page's own poll: not counted
    call(base + "/healthz")
    got = call(base + "/sim/paths")[1]
    srv.shutdown()
    assert got["paths"]["GET /rest/api/2/serverInfo"] == 2
    assert got["unknown"] == {"GET /rest/api/2/nothing-here?secret": 1}
    assert "abc" not in json.dumps(got) and "/healthz" not in json.dumps(got) and "/sim/" not in json.dumps(got)


def test_bcm_counts_the_call_name_and_marks_unknown_calls_but_never_the_arguments():
    m = load("bcm")
    srv, base = serve(m.make_handler(m.Cluster(m.load_nodes(ROOT / "sim.yaml"))))
    m.SHAPES.reset()
    call(base + "/json", {"service": "cmdevice", "call": "getDevices"})
    call(base + "/json", {"service": "cmdevice", "call": "getDevice", "args": ["cn001"]})
    call(base + "/json", {"service": "cmjob", "call": "getJobs", "args": ["secret-arg"]})
    call(base + "/nope")
    got = call(base + "/sim/paths")[1]
    srv.shutdown()
    assert got["paths"]["POST /json cmdevice.getDevices"] == 1 and got["paths"]["POST /json cmdevice.getDevice"] == 1
    assert got["unknown"] == {"POST /json cmjob.getJobs": 1, "GET /nope": 1}
    assert "secret-arg" not in json.dumps(got) and "cn001" not in json.dumps(got)


def test_bmc_counts_redfish_paths_by_shape():
    m = load("bmc")
    srv, base = serve(m.make_handler(m.Farm(m.load_nodes(ROOT / "sim.yaml"))))
    m.SHAPES.reset()
    call(base + "/cn001/redfish/v1/Systems/1")
    call(base + "/cn001/redfish/v1/Chassis/1/Power")  # not served: a 404 a client would hit
    got = call(base + "/sim/paths")[1]
    srv.shutdown()
    assert got["paths"]["GET /{id}/redfish/v1/Systems/1"] == 1
    assert "GET /{id}/redfish/v1/Chassis/1/Power" in got["unknown"] or "GET /{id}/redfish/v1/Chassis/1/Power" in got["paths"]


# --- who asked, how it ended and how long it took (issue 501) ---


def test_the_caller_is_the_user_agent_product_token_and_nothing_else():
    m = load("jira")
    h = lambda ua: {"User-Agent": ua} if ua is not None else {}
    assert m.caller(h("python-requests/2.31.0")) == "python-requests"
    assert m.caller(h("curl/8.5.0 (x86_64-pc-linux-gnu) libcurl/8.5.0")) == "curl"
    assert m.caller(h("Mozilla/5.0 (X11; Linux)")) == "mozilla"
    assert m.caller(h(None)) == "unknown" and m.caller(h("")) == "unknown" and m.caller(h("/weird")) == "unknown"
    assert len(m.caller(h("a" * 80 + "/1"))) <= 24
    assert "secret" not in m.caller(h("tool-name secret-token/1"))  # the token stops at the first space or slash


def test_a_stream_counts_status_classes_and_buckets_latency():
    m = load("jira")
    s = m.Shapes()
    for code, ms in ((200, 3), (200, 40), (200, 2000), (404, 8), (503, 600)):
        s.add("GET", "/rest/api/2/serverInfo", code, who="curl", ms=ms, now=100.0)
    t = s.traffic()
    row, = t["streams"]
    assert (row["who"], row["shape"], row["n"], row["ok"], row["client_error"], row["server_error"]) == ("curl", "GET /rest/api/2/serverInfo", 5, 3, 1, 1)
    assert sum(row["hist"]) == 5 and len(row["hist"]) == len(t["edges"]) + 1
    assert row["hist"][0] == 1 and row["hist"][-1] == 1  # 3 ms in the first bucket, 2000 ms above the last edge


def test_two_callers_of_one_path_are_two_streams_and_the_ring_is_capped_and_filterable():
    m = load("bcm")
    s = m.Shapes()
    for i in range(m.RING_CAP + 50):
        s.add("POST", "/json", 200, label="cmdevice.getDevices", who="a" if i % 2 else "b", now=1000.0 + i)
    t = s.traffic()
    assert {r["who"] for r in t["streams"]} == {"a", "b"} and sum(r["n"] for r in t["streams"]) == m.RING_CAP + 50
    assert len(t["recent"]) == m.RING_CAP and t["recent"][-1][0] == 1000.0 + m.RING_CAP + 49  # the oldest 50 fell off
    assert all(r[0] > 1400.0 for r in s.traffic(since=1400.0)["recent"])


def test_traffic_is_served_read_only_and_does_not_count_itself():
    m = load("jira")
    srv, base = serve(m.make_handler(m.Jira(deliver=lambda *a: None)))
    m.SHAPES.reset()
    hdr = {"Authorization": "Bearer x", "User-Agent": "python-requests/2.31.0"}
    call(base + "/rest/api/2/serverInfo", headers=hdr)
    call(base + "/rest/api/2/serverInfo", headers={**hdr, "X-Workbench-Probe": "1"})  # the page's own poll
    first = call(base + "/sim/traffic")[1]
    again = call(base + "/sim/traffic")[1]
    since = call(base + f"/sim/traffic?since={first['now'] + 1}")[1]
    srv.shutdown()
    assert [r["who"] for r in first["streams"]] == ["python-requests"] and first["streams"][0]["n"] == 1
    assert again["streams"] == first["streams"] and len(first["recent"]) == 1
    assert since["recent"] == [] and since["streams"] == first["streams"]
    assert "Bearer" not in json.dumps(first) and "2.31.0" not in json.dumps(first)


def test_every_mock_serves_traffic_with_a_measured_latency():
    for name, make in (("bcm", lambda m: m.make_handler(m.Cluster(m.load_nodes(ROOT / "sim.yaml")))),
                       ("bmc", lambda m: m.make_handler(m.Farm(m.load_nodes(ROOT / "sim.yaml"))))):
        m = load(name)
        srv, base = serve(make(m))
        m.SHAPES.reset()
        path = "/json" if name == "bcm" else "/cn001/redfish/v1/Systems/1"
        call(base + path, {"service": "cmdevice", "call": "getDevices"} if name == "bcm" else None, headers={"User-Agent": "curl/8.5.0"})
        t = call(base + "/sim/traffic")[1]
        srv.shutdown()
        assert t["streams"][0]["who"] == "curl" and t["recent"][0][1] == "curl", name
        assert 0 <= t["recent"][0][4] < 1000, name  # milliseconds between reading the request and answering it
