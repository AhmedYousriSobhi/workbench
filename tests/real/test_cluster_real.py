"""The Cluster Canvas against the live stack: state comes from the real mocks and a click changes them."""
import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import stack

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ui():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    token = proc.stdout.readline().split("token=")[1].strip()
    base, h = f"http://127.0.0.1:{port}", {"Authorization": f"Bearer {token}", "X-Requested-With": "workbench"}

    def ready() -> bool:
        try:
            snap = api(base, h, "/api/cluster")
            return all(snap["services"].values()) and all(n["state"] == "ok" for n in snap["nodes"])
        except OSError:
            return False

    assert wait_for(ready, timeout=60), "mocks never became healthy"
    yield base, h, token
    proc.terminate()
    proc.wait(timeout=10)
    stack.down(say=lambda m: None)


def api(base, h, path, method="GET"):
    r = urllib.request.Request(base + path, headers=h, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())


def node(base, h, name):
    return next(n for n in api(base, h, "/api/cluster")["nodes"] if n["name"] == name)


def test_healthy_stack_shows_six_ok_nodes_and_all_services_up(ui):
    base, h, _ = ui
    snap = api(base, h, "/api/cluster")
    assert [n["name"] for n in snap["nodes"]] == ["cn001", "cn002", "cn003", "cn004", "gpu001", "gpu002"]
    assert all(n["state"] == "ok" and n["bcm"] == "UP" for n in snap["nodes"])
    assert snap["services"] == {"bmc-farm": True, "bcm-mock": True, "jira-mock": True, "gpu-exporter": True}


def test_each_action_changes_what_the_real_mocks_report(ui):
    base, h, _ = ui
    api(base, h, "/api/nodes/cn002/power-off", "POST")
    n = node(base, h, "cn002")
    assert (n["state"], n["power"], n["bcm"]) == ("down", "Off", "DOWN")
    api(base, h, "/api/nodes/cn003/fan-fail", "POST")
    assert (node(base, h, "cn003")["state"], node(base, h, "cn003")["fans_failed"]) == ("warn", 3)
    api(base, h, "/api/nodes/cn004/overheat", "POST")
    assert node(base, h, "cn004")["state"] == "fault" and node(base, h, "cn004")["temp"] >= 90
    api(base, h, "/api/nodes/gpu001/gpu-xid", "POST")
    assert node(base, h, "gpu001")["xid"] == 79 and node(base, h, "gpu001")["state"] == "fault"
    for name in ("cn002", "cn003", "cn004", "gpu001"):
        api(base, h, f"/api/nodes/{name}/clear", "POST")
    assert all(n["state"] == "ok" for n in api(base, h, "/api/cluster")["nodes"])  # clear restores everything
    api(base, h, "/api/nodes/cn002/power-off", "POST")
    api(base, h, "/api/nodes/cn002/power-on", "POST")
    assert node(base, h, "cn002")["state"] == "ok"


def test_screenshot_of_a_faulted_cluster(ui, tmp_path):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    base, h, token = ui
    for name, action in (("cn002", "power-off"), ("cn003", "fan-fail"), ("gpu001", "gpu-xid"), ("gpu002", "overheat")):
        api(base, h, f"/api/nodes/{name}/{action}", "POST")
    out = Path(os.environ.get("BENCHCTL_SHOT_DIR", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vp, size in (("desktop", (1280, 900)), ("phone", (390, 800))):
            page = browser.new_page(viewport={"width": size[0], "height": size[1]})
            page.goto(f"{base}/login?token={token}")
            page.wait_for_selector(".node.fault")
            page.click("details[data-node=cn001] summary")  # opening an actions menu must survive the 3 s refresh
            page.wait_for_timeout(3500)
            assert page.get_attribute("details[data-node=cn001]", "open") is not None
            assert page.locator(".node.down").count() == 1 and page.locator(".node.warn").count() == 1
            assert page.locator(".node.fault").count() == 2
            page.screenshot(path=str(out / f"cluster-{vp}.png"), full_page=True)
            assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        browser.close()
    for name in ("cn002", "cn003", "gpu001", "gpu002"):
        api(base, h, f"/api/nodes/{name}/clear", "POST")
