"""Documentation images with sample data: the page's own cluster, flow and connection calls are answered locally, so no
cluster is needed. Writes the manual's screenshots (the wiki and the in-page help use the same files) and the README's
animated GIF into docs/manual/img.

    .venv/bin/python scripts/doc_shots.py          # needs `pip install -e '.[browser]'` and `playwright install chromium`

Re-run it after a change to the page's look, then commit the images."""
import io
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent; OUT = ROOT / "docs/manual/img"
nodes = [{"name": n, "kind": "compute", "state": "ok", "label": "Healthy", "temp": 45 + i, "power": "On", "gpus": 0, "bcm": "UP"} for i, n in enumerate(["cn001", "cn002", "cn003", "cn004"])] + \
        [{"name": n, "kind": "gpu", "state": "ok", "label": "Healthy", "temp": 44 + i, "power": "On", "gpus": 8, "gpu_util": 3 + i, "gpus_busy": 0, "bcm": "UP"} for i, n in enumerate(["gpu001", "gpu002"])]
cluster = {"logins": [{"name": "login", "mode": "ssh", "port": 2222}, {"name": "login2", "mode": "teleport", "port": None}, {"name": "login3", "mode": "both", "port": 2224}], "services": {"bmc-farm": True, "bcm-mock": True, "jira-mock": True, "gpu-exporter": True, "teleport": True}, "nodes": nodes,
           "actions": [{"id": "power-off", "label": "Power off", "gpu_only": False}, {"id": "clear", "label": "Clear faults", "gpu_only": False}]}
sys.path.insert(0, str(ROOT))
import yaml

from webui import connections

SIM = {**yaml.safe_load((ROOT / "sim.yaml").read_text()), "login": ["ssh", "teleport", "both"]}
FLOW = """if (flowEs) flowEs.close(); flowSnap = {reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true, teleport: true}, totals: {'bmc-farm': 412, 'bcm-mock': 96, 'jira-mock': 31, 'gpu-exporter': 1180, teleport: 7}, faults: 0};
          recent = [{t: Date.now(), to: 'teleport', n: 3}, {t: Date.now(), to: 'bmc-farm', n: 12}, {t: Date.now(), to: 'gpu-exporter', n: 30}]; paintBlocks();
          onFlow({level: 0, cursor: 99, events: [{from: 'clients', to: 'teleport', n: 2, label: 'requests', nodes: []}, {from: 'clients', to: 'gpu-exporter', n: 2, label: 'requests', nodes: []}], totals: flowSnap.totals, reachable: flowSnap.reachable});"""
port = 8199
proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
try:
    url = proc.stdout.readline().split("UI: ")[1].strip()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
        pg.route("**/api/cluster", lambda r: r.fulfill(json=cluster))
        pg.route("**/api/flow*", lambda r: r.abort())
        pg.route("**/api/connections", lambda r: r.fulfill(json={"entities": connections.entities(SIM)}))
        def settings(r):
            j = r.fetch().json(); j["logins"]["now"] = ["ssh", "teleport", "both"]; r.fulfill(json=j)
        pg.route("**/api/settings", settings)
        pg.goto(url); pg.wait_for_selector("#flow rect.blk"); pg.wait_for_timeout(1200)
        pg.evaluate(FLOW)
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(OUT / "web-infrastructure.png"))
        pg.hover("#lg-login2"); pg.wait_for_timeout(250); pg.screenshot(path=str(OUT / "web-architecture-hover.png")); pg.mouse.move(2, 2)
        pg.click("#nd-gpu001"); pg.wait_for_selector("#pop dl"); pg.wait_for_timeout(500); pg.screenshot(path=str(OUT / "web-architecture-popup.png")); pg.keyboard.press("Escape")
        pg.click("#treetoggle"); pg.wait_for_timeout(400); pg.screenshot(path=str(OUT / "web-architecture-wide.png")); pg.click("#treetoggle"); pg.wait_for_timeout(300)
        pg.click("#tb-conn"); pg.wait_for_selector("#conn-rows tr td code"); pg.wait_for_timeout(300)
        pg.locator("#conn-rows tr[data-id='teleport']").scroll_into_view_if_needed()
        pg.evaluate("document.querySelector('#conn-rows tr[data-id=teleport]').classList.add('focus')")
        pg.evaluate("document.querySelector('#conn-rows tr[data-id=teleport]').classList.remove('focus')")
        pg.screenshot(path=str(OUT / "web-teleport-connection.png"))
        pg.evaluate("document.querySelector('#conn-rows tr[data-id=login2]').classList.add('focus'); window.scrollTo(0, 0)")
        pg.screenshot(path=str(OUT / "web-login-connections.png"))
        pg.click("#tb-nodes"); pg.click("#menubtn"); pg.click("#planbtn"); pg.wait_for_selector("#pl-parts input"); pg.wait_for_timeout(800)
        pg.screenshot(path=str(OUT / "web-cluster-architecture.png"))
        pg.locator("#pl-logins").scroll_into_view_if_needed(); pg.wait_for_timeout(300)
        pg.screenshot(path=str(OUT / "web-login-nodes.png"))
        # the README's animation: the diagram alone, wide, with real-looking traffic and a fault landing on a node
        from PIL import Image
        pg.goto(url); pg.wait_for_selector("#flow rect.blk"); pg.wait_for_timeout(1200)
        pg.evaluate("setTree(false)"); pg.evaluate(FLOW); pg.wait_for_timeout(300)
        clip = pg.locator("#trafficpanel").bounding_box(); frames = []
        script = {2: "['teleport', 2]", 6: "['bmc-farm', 3]", 10: "['gpu-exporter', 3]", 14: "['jira-mock', 2]", 17: "FAULT", 24: "['teleport', 3]"}
        for i in range(36):
            if i in script:
                ev = ("{from: 'injector', to: 'bmc-farm', n: 1, label: 'x', nodes: ['gpu001', 'cn003']}" if script[i] == "FAULT"
                      else f"{{from: 'clients', to: {script[i]}[0], n: {script[i]}[1], label: 'requests', nodes: []}}")
                pg.evaluate(f"onFlow({{level: 0, cursor: {200 + i}, events: [{ev}], totals: flowSnap.totals, reachable: flowSnap.reachable}})")
            frames.append(Image.open(io.BytesIO(pg.screenshot(clip=clip))).convert("RGB")); pg.wait_for_timeout(60)
        w = 900; frames = [f.resize((w, round(f.height * w / f.width)), Image.LANCZOS).quantize(96) for f in frames]
        frames[0].save(OUT / "architecture-demo.gif", save_all=True, append_images=frames[1:], duration=140, loop=0, optimize=True)
        b.close()
finally:
    proc.terminate()
