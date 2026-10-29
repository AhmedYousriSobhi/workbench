import importlib.util
import json
import random
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exporter = load("components/gpu-exporter-farm/exporter.py", "exporter")
gen = load("observability/gen_targets.py", "gen_targets")


def test_nodes_from_sim_yaml():
    nodes = exporter.load_nodes(ROOT / "sim.yaml")
    assert nodes["cn001"] == 0 and nodes["gpu001"] == 8
    assert len(nodes) == 6


def test_render_has_gpu_and_fs_metrics():
    s = exporter.State(exporter.load_nodes(ROOT / "sim.yaml"), random.Random(1))
    text = s.render()
    assert text.count("DCGM_FI_DEV_GPU_UTIL{") == 16
    assert 'node_filesystem_free_bytes{node="cn001",mountpoint="/mnt/lustre"}' in text


def test_inject_xid_and_clear():
    s = exporter.State({"gpu001": 1}, random.Random(1))
    s.inject({"node": "gpu001", "xid": 79, "temp": 92})
    t = s.render()
    assert 'DCGM_FI_DEV_XID_ERRORS{node="gpu001",gpu="0"} 79' in t
    assert 'DCGM_FI_DEV_GPU_TEMP{node="gpu001",gpu="0"} 92.0' in t
    s.inject({"node": "gpu001", "clear": True})
    assert 'XID_ERRORS{node="gpu001",gpu="0"} 0' in s.render()


def test_inject_unknown_node():
    s = exporter.State({"gpu001": 1})
    try:
        s.inject({"node": "nope", "xid": 1})
    except KeyError:
        return
    raise AssertionError


def test_gen_targets_names_agree():
    t = gen.targets(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert t[0]["labels"]["nodes"] == "cn001,cn002,cn003,cn004,gpu001,gpu002"


def test_dashboard_json_valid():
    d = json.loads((ROOT / "observability/dashboards/hpc-overview.json").read_text())
    assert d["uid"] == "hpc-overview" and len(d["panels"]) == 6


def test_http_handler_serves_metrics_injects_and_rejects_bad_requests():
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    state = exporter.State(exporter.load_nodes(ROOT / "sim.yaml"), random.Random(1))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), exporter.make_handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def post(path, body):
        req = Request(base + path, data=json.dumps(body).encode(), method="POST")
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except HTTPError as e:
            return e.code, e.read()

    try:
        with urlopen(base + "/metrics", timeout=5) as r:
            assert r.status == 200 and b"DCGM_FI_DEV_GPU_UTIL" in r.read()
        def xid_lines():
            with urlopen(base + "/metrics", timeout=5) as r:
                return [ln for ln in r.read().decode().splitlines()
                        if ln.startswith("DCGM_FI_DEV_XID_ERRORS") and 'gpu001' in ln]

        assert all(ln.endswith(" 0") for ln in xid_lines())  # nothing injected yet
        assert post("/sim/inject", {"node": "gpu001", "xid": 79})[0] == 200
        assert all(ln.endswith(" 79") for ln in xid_lines()) and xid_lines()  # the scrape now reports it
        assert post("/sim/inject", {"node": "nope", "xid": 1})[0] == 400  # unknown node
        assert post("/sim/inject", {"node": "gpu001", "xid": "x"})[0] == 400  # not a number
        assert post("/other", {})[0] == 404
        with pytest.raises(HTTPError) as e:
            urlopen(base + "/nothing", timeout=5)
        assert e.value.code == 404
    finally:
        srv.shutdown()
