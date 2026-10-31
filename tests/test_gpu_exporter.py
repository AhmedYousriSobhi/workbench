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


def series(text: str, metric: str) -> list[tuple[dict, str]]:
    """[(labels, value)] for one metric from a Prometheus text scrape."""
    import re
    out = []
    for line in text.splitlines():
        m = re.fullmatch(metric + r"\{(.*)\} (\S+)", line)
        if m:
            out.append((dict(re.findall(r'(\w+)="([^"]*)"', m.group(1))), m.group(2)))
    return out


def test_gpu_series_carry_the_labels_of_the_real_dcgm_exporter():
    s = exporter.State({"gpu001": 2}, random.Random(1), "h200")
    for labels, _ in series(s.render(), "DCGM_FI_DEV_GPU_UTIL"):
        assert labels["Hostname"] == labels["node"] == "gpu001"
        assert labels["modelName"] == "NVIDIA H200" and labels["device"] == f"nvidia{labels['gpu']}"
    uuids = {lb["UUID"] for lb, _ in series(s.render(), "DCGM_FI_DEV_GPU_UTIL")}
    assert len(uuids) == 2


def test_used_and_free_framebuffer_add_up_to_the_gpu_total():
    s = exporter.State({"gpu001": 3}, random.Random(2), "h200")
    text = s.render()
    used = {lb["gpu"]: float(v) for lb, v in series(text, "DCGM_FI_DEV_FB_USED")}
    free = {lb["gpu"]: float(v) for lb, v in series(text, "DCGM_FI_DEV_FB_FREE")}
    assert used.keys() == free.keys() == {"0", "1", "2"}
    assert {used[g] + free[g] for g in used} == {exporter.FB_TOTAL_MIB["h200"]}
    assert all(f > 0 for f in free.values())


def test_gpu_type_comes_from_sim_yaml_and_unknown_types_get_a_default():
    assert exporter.load_gpu_type(ROOT / "sim.yaml") == "h200"
    s = exporter.State({"gpu001": 1}, random.Random(1), "mystery")
    assert s.fb_total == exporter.FB_DEFAULT_MIB and s.model == "MYSTERY"
    assert exporter.State({"gpu001": 1}).model == "NVIDIA GPU"


def test_render_has_gpu_and_fs_metrics():
    s = exporter.State(exporter.load_nodes(ROOT / "sim.yaml"), random.Random(1))
    text = s.render()
    assert text.count("DCGM_FI_DEV_GPU_UTIL{") == 16
    assert 'node_filesystem_free_bytes{node="cn001",mountpoint="/mnt/lustre"}' in text


def test_inject_xid_and_clear():
    s = exporter.State({"gpu001": 1}, random.Random(1))
    s.inject({"node": "gpu001", "xid": 79, "temp": 92})
    t = s.render()
    assert series(t, "DCGM_FI_DEV_XID_ERRORS")[0][1] == "79" and series(t, "DCGM_FI_DEV_GPU_TEMP")[0][1] == "92.0"
    s.inject({"node": "gpu001", "clear": True})
    assert series(s.render(), "DCGM_FI_DEV_XID_ERRORS")[0][1] == "0"


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


def test_a_powered_off_node_publishes_nothing_until_powered_on():
    s = exporter.State({"gpu001": 2, "gpu002": 2}, random.Random(1), "h200")
    s.inject({"node": "gpu001", "down": True})
    text = s.render()
    assert 'Hostname="gpu001"' not in text and 'node="gpu001"' not in text and 'Hostname="gpu002"' in text
    s.inject({"node": "gpu001", "clear": True})  # clearing faults does not power it on
    assert 'node="gpu001"' not in s.render()
    s.inject({"node": "gpu001", "down": False})
    assert 'Hostname="gpu001"' in s.render()
