"""Issue 414: the Traffic & Communication view follows node state. The page's cluster and Slurm calls are answered locally
(no cluster needed), a node is drained, and the diagram, the table and the timeline must change without a reload."""
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from webui import graph

NAMES = ["cn001", "cn002", "cn003", "cn004"]
NODES = [{"name": n, "kind": "compute", "state": "ok", "label": "Healthy", "temp": 45, "power": "On", "gpus": 0, "bcm": "UP"} for n in NAMES]
SERVICES = {"slurmctld": True, "bmc-farm": True, "bcm-mock": True, "prometheus": True, "grafana": True}


def cluster(nodes):
    base = {"services": SERVICES, "nodes": nodes, "logins": [{"name": "login", "mode": "ssh", "port": 2222, "ip": "10.141.255.254"}],
            "actions": [{"id": "clear", "label": "Clear faults", "gpu_only": False}]}
    return graph.attach(base)


def stats(states):
    return {"stats": {"rpcs": 10, "threads": 1, "top": [], "dbd": None, "node_states": states}}


def test_a_drained_node_shows_in_the_traffic_diagram_and_timeline():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    try:
        url = proc.stdout.readline().split("UI: ")[1].strip() + "?token=" + (ROOT / "compose/generated/secrets/ui_token").read_text().strip()
        slurm = {"s": {n: "idle" for n in NAMES}}
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.route("**/api/cluster", lambda r: r.fulfill(json=cluster(NODES)))
            pg.route("**/api/slurm-stats", lambda r: r.fulfill(json=stats(slurm["s"])))
            pg.route("**/api/flow*", lambda r: r.abort())
            pg.add_init_script("try { localStorage.setItem('workbench-circuit', '0'); } catch (e) {}")  # the classic diagram and list: the circuit is the default view
            pg.goto(url)
            pg.click("#tb-traffic")
            pg.wait_for_selector("#tr-map text")
            pg.wait_for_function("document.querySelector('#tr-map').textContent.includes('nodes 4/4')", timeout=15000)
            assert "▲" not in pg.text_content("#tr-map")
            slurm["s"] = {**slurm["s"], "cn002": "drained"}  # what `scontrol update nodename=cn002 state=drain` makes sinfo say
            pg.wait_for_function("document.querySelector('#tr-map').textContent.includes('nodes 3/4')", timeout=15000)
            assert "▲" in pg.text_content("#tr-map") and "1 of 4 nodes out" in pg.text_content("#tr-rows")
            assert "cn002: Slurm: drained" in pg.text_content("#tr-feed")
            slurm["s"] = {n: "idle" for n in NAMES}  # resume
            pg.wait_for_function("document.querySelector('#tr-map').textContent.includes('nodes 4/4')", timeout=15000)
            assert "cn002: back to normal" in pg.text_content("#tr-feed")
            b.close()
    finally:
        proc.terminate()
