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
