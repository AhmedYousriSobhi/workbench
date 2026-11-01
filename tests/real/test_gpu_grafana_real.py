"""What a monitoring client does for GPU usage, against the live stack: a read-only Grafana token and
/api/ds/query on the Prometheus datasource, filtered by the Hostname label, reading util, used, free and temp."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import stack

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = "http://127.0.0.1:3000"
METRICS = {"util": "DCGM_FI_DEV_GPU_UTIL", "used": "DCGM_FI_DEV_FB_USED",
           "free": "DCGM_FI_DEV_FB_FREE", "temp": "DCGM_FI_DEV_GPU_TEMP"}


def api(path: str, token: str, body: dict | None = None):
    req = urllib.request.Request(GRAFANA + path, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def gpu_query(token: str, hosts: list[str]):
    regex = "|".join(hosts)
    return api("/api/ds/query", token, {"from": "now-5m", "to": "now", "queries": [
        {"refId": k, "datasource": {"uid": "prometheus"}, "expr": f'{m}{{Hostname=~"{regex}"}}',
         "instant": True, "range": False} for k, m in METRICS.items()]})


@pytest.fixture(scope="module")
def token():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    tok = (ROOT / stack.VIEWER_TOKEN_FILE).read_text().strip()
    # Prometheus scrapes every 15 s: wait for the first samples of every metric
    assert wait_for(lambda: all(len(series(gpu_query(tok, ["gpu001", "gpu002"])[1], k)) == 16 for k in METRICS), timeout=90)
    yield tok
    stack.down(say=lambda m: None)


def series(response: dict, ref: str) -> list[dict]:
    """The labels of each series of one query, as a monitoring client reads them from the frames."""
    frames = response.get("results", {}).get(ref, {}).get("frames", [])
    return [f["labels"] for fr in frames for f in fr["schema"]["fields"] if f.get("labels")]


def test_all_four_series_exist_per_gpu_with_the_labels_a_client_reads(token):
    status, resp = gpu_query(token, ["gpu001", "gpu002"])
    assert status == 200
    for ref in METRICS:
        labels = series(resp, ref)
        assert len(labels) == 16, ref  # 2 nodes x 8 GPUs
        assert {lb["Hostname"] for lb in labels} == {"gpu001", "gpu002"}
        assert all(lb["gpu"] in {str(i) for i in range(8)} and lb["modelName"] == "NVIDIA H200" for lb in labels)


def test_total_framebuffer_is_used_plus_free(token):
    _, resp = gpu_query(token, ["gpu001"])
    values = {}
    for ref in ("used", "free"):
        for fr in resp["results"][ref]["frames"]:
            for f, col in zip(fr["schema"]["fields"], fr["data"]["values"], strict=False):
                if f.get("labels"):
                    values.setdefault(f["labels"]["gpu"], {})[ref] = float(col[-1])
    assert len(values) == 8 and all(v["used"] > 0 and v["free"] > 0 for v in values.values())


def test_the_viewer_token_reads_but_cannot_write(token):
    assert api("/api/org", token)[0] == 200
    assert api("/api/annotations", token, {"text": "x", "tags": ["t"]})[0] == 403


def test_host_filter_narrows_the_answer(token):
    _, resp = gpu_query(token, ["gpu002"])
    assert {lb["Hostname"] for lb in series(resp, "util")} == {"gpu002"}
