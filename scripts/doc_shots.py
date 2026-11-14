"""Documentation images with sample data: the page's own cluster, flow and connection calls are answered locally, so no
cluster is needed. Writes the manual's screenshots (the wiki and the in-page help use the same files) and the README's
animated GIF into docs/manual/img.

    .venv/bin/python scripts/doc_shots.py          # needs `pip install -e '.[browser]'` and `playwright install chromium`

Every image comes as a pair, `NAME-light.png` and `NAME-dark.png` (the GIF as `architecture-demo-light.gif` and `-dark.gif`): pages
use the light one (GitHub's default), the README shows both in a `<picture>`. Dark is the page's default theme (Midnight). With no
argument it runs both schemes in turn; `doc_shots.py light` or `dark` runs one. Re-run it after a change to the page's look, then
commit the images."""
import io
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent; OUT = ROOT / "docs/manual/img"
SCHEMES = {"light": "light", "dark": "midnight"}  # file suffix: the page theme it shows
if len(sys.argv) < 2:
    for _scheme in SCHEMES:
        subprocess.run([sys.executable, __file__, _scheme], check=True)
    sys.exit(0)
SCHEME = sys.argv[1]
assert SCHEME in SCHEMES, f"usage: doc_shots.py [{'|'.join(SCHEMES)}]"
nodes = [{"name": n, "kind": "compute", "state": "ok", "label": "Healthy", "temp": 45 + i, "power": "On", "gpus": 0, "bcm": "UP"} for i, n in enumerate(["cn001", "cn002", "cn003", "cn004"])] + \
        [{"name": n, "kind": "gpu", "state": "ok", "label": "Healthy", "temp": 44 + i, "power": "On", "gpus": 8, "gpu_util": 3 + i, "gpus_busy": 0, "bcm": "UP"} for i, n in enumerate(["gpu001", "gpu002"])]
cluster = {"logins": [{"name": "login", "mode": "ssh", "port": 2222, "ip": "10.141.255.254"}, {"name": "login2", "mode": "teleport", "port": None, "ip": "10.141.255.252"}, {"name": "login3", "mode": "both", "port": 2224, "ip": "10.141.255.251"}], "services": {"bmc-farm": True, "bcm-mock": True, "jira-mock": True, "gpu-exporter": True, "teleport": True, "jump": True, "controller": True}, "nodes": nodes,
           "actions": [{"id": "power-off", "label": "Power off", "gpu_only": False}, {"id": "clear", "label": "Clear faults", "gpu_only": False}]}
sys.path.insert(0, str(ROOT))
import yaml

from webui import blast, connections, graph
from webui.app import login_url

cluster = {**graph.attach(cluster), "blast": blast.payload()}  # the declared graph and the blast table the page draws from (#312, #315)
SIM = {**yaml.safe_load((ROOT / "sim.yaml").read_text()), "login": ["ssh", "teleport", "both"]}
FLOW = """if (flowEs) flowEs.close(); flowSnap = {reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true, teleport: true}, totals: {'bmc-farm': 412, 'bcm-mock': 96, 'jira-mock': 31, 'gpu-exporter': 1180, teleport: 7}, errors: {'bmc-farm': 2, 'bcm-mock': 0, 'jira-mock': 0, 'gpu-exporter': 0}, faults: 0};
          recent = [{t: Date.now(), to: 'teleport', n: 3}, {t: Date.now(), to: 'bmc-farm', n: 12}, {t: Date.now(), to: 'gpu-exporter', n: 30}]; paintBlocks();
          onFlow({level: 0, cursor: 99, events: [{from: 'clients', to: 'teleport', n: 2, label: 'requests', nodes: []}, {from: 'clients', to: 'gpu-exporter', n: 2, label: 'requests', nodes: []}], totals: flowSnap.totals, errors: flowSnap.errors, reachable: flowSnap.reachable});"""

def traffic_route(calls=[0]):  # noqa: B006  sample /api/traffic data whose counts grow between polls, so rates can be measured
    import time as _t

    def row(who, svc, shape, n, ok, ce, se, hist):
        return {"who": who, "service": svc, "shape": shape, "n": n, "ok": ok, "client_error": ce, "server_error": se, "ms": n * 4.0, "hist": hist}

    def answer(route):
        calls[0] += 1
        k, now = calls[0], _t.time()
        streams = [row("python-requests", "bmc-farm", "GET /{id}/redfish/v1/Systems/1", 200 + k * 6, 200 + k * 6, 0, 0, [150 + k * 6, 40, 10, 0, 0, 0, 0, 0, 0]),
                   row("python-requests", "jira-mock", "GET /rest/api/2/search?jql", 90 + k * 2, 80 + k * 2, 0, 10, [20, 30, 20, 10, 5, 3, 2, 0, 0]),
                   row("curl", "bcm-mock", "POST /json cmdevice.getDevices", 40 + k, 40 + k, 0, 0, [40 + k, 0, 0, 0, 0, 0, 0, 0, 0]),
                   row("unknown", "jira-mock", "GET /rest/api/2/serverInfo", 60 + k, 55 + k, 5, 0, [50 + k, 10, 0, 0, 0, 0, 0, 0, 0])]
        recent = [[round(now - 300 + i * 5, 3), "unknown", "jira-mock", "GET /rest/api/2/serverInfo", 200, 3.0] for i in range(60)]
        recent += [[round(now - 300 + i * 30, 3), "python-requests", "bmc-farm", "GET /{id}/redfish/v1/Systems/1", 200, 8.0] for i in range(10)]
        recent += [[round(now - t, 3), "python-requests", "jira-mock", "GET /rest/api/2/search?jql", 503, 600.0] for t in (50, 120, 200)]
        route.fulfill(json={"now": round(now, 3), "edges": [5, 10, 25, 50, 100, 250, 500, 1000], "services": {"bmc-farm": True, "bcm-mock": True, "jira-mock": True},
                            "streams": streams, "recent": sorted(recent) if k == 1 else []})
    return answer


def capture_route():  # sample /api/capture answers: a few Slurm messages and one the parser cannot read
    import time as _t

    def answer(route):
        url, now = route.request.url, _t.time()
        if url.endswith(("/api/capture/start", "/api/capture/stop")):
            return route.fulfill(json={"ok": True, "node": "gpu001", "seconds": 300, "running": False})
        base = {"peer": "10.141.255.1", "peer_name": "slurmctld", "port": 6818, "version": 11520, "flags": 0}
        msgs = [{**base, "t": now - 6, "dir": "in", "size": 120, "type": "REQUEST_PING", "type_id": 1008, "kind": "request", "latency_ms": None, "body_length": 8},
                {**base, "t": now - 5.99, "dir": "out", "size": 70, "type": "RESPONSE_SLURM_RC", "type_id": 8001, "kind": "response", "latency_ms": 9.5, "body_length": 4},
                {**base, "t": now - 3, "dir": "out", "size": 940, "type": "MESSAGE_NODE_REGISTRATION_STATUS", "type_id": 1002, "kind": "message", "latency_ms": None, "port": 6817, "body_length": 900},
                {"t": now - 1, "dir": "in", "peer": "10.141.0.9", "peer_name": "login", "port": 6818, "size": 63, "type": "unknown", "type_id": None, "kind": "unknown", "latency_ms": None, "note": "the header does not parse as a Slurm 26.05.5 message"}]
        route.fulfill(json={"ok": True, "running": True, "node": "gpu001", "messages": msgs, "next": 4, "ended": None, "error": None, "seconds_left": 262, "class": "P5"})
    return answer

port = 8199
proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
try:
    base_url = proc.stdout.readline().split("UI: ")[1].strip().removesuffix("/login")
    ui_token = (ROOT / "compose/generated/secrets/ui_token").read_text().strip()  # a one-time code, no token in the URL (#466)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 900}, color_scheme=SCHEME)
        pg.add_init_script("window.__WB_TEST = true; try { localStorage.setItem('workbench-circuit', '0'); localStorage.setItem('workbench-theme', '" + SCHEMES[SCHEME] + "'); sessionStorage.getItem('workbench-tab') || sessionStorage.setItem('workbench-tab', 'nodes'); } catch (e) {}")  # exposes the page modules as globals (webui/static/js/testhook.js)
        pg.route("**/api/cluster", lambda r: r.fulfill(json=cluster))
        pg.route("**/api/flow*", lambda r: r.abort())
        pg.route("**/api/connections", lambda r: r.fulfill(json={"entities": connections.entities(SIM)}))
        def settings(r):
            j = r.fetch().json(); j["logins"]["now"] = ["ssh", "teleport", "both"]; r.fulfill(json=j)
        pg.route("**/api/settings", settings)
        pg.goto(login_url(base_url, ui_token)); pg.wait_for_selector("#flow rect.blk"); pg.wait_for_timeout(1200)
        pg.evaluate("setTree(true, false)"); pg.evaluate(FLOW)
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(OUT / f"web-infrastructure-{SCHEME}.png"))
        pg.hover("#lg-login2"); pg.wait_for_timeout(250); pg.screenshot(path=str(OUT / f"web-architecture-hover-{SCHEME}.png")); pg.mouse.move(2, 2)
        pg.click("#nd-gpu001"); pg.wait_for_selector("#pop dl"); pg.wait_for_timeout(500); pg.screenshot(path=str(OUT / f"web-architecture-popup-{SCHEME}.png")); pg.keyboard.press("Escape")
        pg.evaluate("setLevel('overview', false)"); pg.wait_for_timeout(500); pg.mouse.move(420, 480); pg.wait_for_timeout(250); pg.screenshot(path=str(OUT / f"web-global-view-{SCHEME}.png")); pg.mouse.move(2, 2); pg.evaluate("setLevel('full', false)"); pg.wait_for_timeout(500)  # the global view (#321)
        pg.evaluate("setTree(false, false)"); pg.wait_for_timeout(400); pg.screenshot(path=str(OUT / f"web-architecture-wide-{SCHEME}.png")); pg.evaluate("setTree(true, false)"); pg.wait_for_timeout(300)
        pg.click("#vw-cv"); pg.wait_for_selector("#flow[data-cv] .cv-box"); pg.wait_for_timeout(600); pg.evaluate("document.querySelector('header.top').style.setProperty('position', 'static', 'important')"); pg.locator("#trafficpanel").screenshot(path=str(OUT / f"web-circuit-{SCHEME}.png")); pg.evaluate("zoomTo('pool', {pool: 'sg'})"); pg.wait_for_timeout(1100); pg.locator("#trafficpanel").screenshot(path=str(OUT / f"web-pool-{SCHEME}.png")); pg.evaluate("zoomTo('fleet')"); pg.wait_for_timeout(1100); pg.locator("#trafficpanel").screenshot(path=str(OUT / f"web-fleet-{SCHEME}.png")); pg.route("**/api/traffic*", traffic_route()); pg.evaluate("zoomTo('cluster'); zoomTo('traffic')"); pg.wait_for_timeout(1500); pg.evaluate("zoomTo('requests')"); pg.wait_for_timeout(8500); pg.locator("#tan").screenshot(path=str(OUT / f"web-traffic-analysis-{SCHEME}.png")); pg.route("**/api/capture/*", capture_route()); pg.evaluate("location.hash = 'workbench-gpu001'"); pg.wait_for_timeout(3500); pg.locator("#an-focus").screenshot(path=str(OUT / f"web-wire-capture-{SCHEME}.png")); pg.evaluate("history.replaceState(null, '', location.pathname + location.search)"); pg.wait_for_timeout(300); pg.unroute("**/api/capture/*"); pg.unroute("**/api/traffic*"); pg.evaluate("zoomTo('fleet')"); pg.wait_for_timeout(1100); pg.evaluate("sessionStorage.removeItem('workbench-level'); setCircuit(false); setView(false, false)"); pg.wait_for_timeout(300)
        pg.click("#tb-conn"); pg.wait_for_selector("#conn-rows tr td code"); pg.wait_for_timeout(300)
        pg.locator("#conn-rows tr[data-id='teleport']").scroll_into_view_if_needed()
        pg.evaluate("document.querySelector('#conn-rows tr[data-id=teleport]').classList.add('focus')")
        pg.evaluate("document.querySelector('#conn-rows tr[data-id=teleport]').classList.remove('focus')")
        pg.screenshot(path=str(OUT / f"web-teleport-connection-{SCHEME}.png"))
        pg.evaluate("document.querySelector('#conn-rows tr[data-id=login2]').classList.add('focus'); window.scrollTo(0, 0)")
        pg.screenshot(path=str(OUT / f"web-login-connections-{SCHEME}.png"))
        pg.click("#tb-nodes"); pg.click("#menubtn"); pg.click("#planbtn"); pg.wait_for_selector("#pl-parts input"); pg.wait_for_timeout(800)
        pg.screenshot(path=str(OUT / f"web-cluster-architecture-{SCHEME}.png"))
        pg.locator("#pl-logins").scroll_into_view_if_needed(); pg.wait_for_timeout(300)
        pg.screenshot(path=str(OUT / f"web-login-nodes-{SCHEME}.png"))
        # the README's animation: every capability in turn, each with a caption (#337). Sample data, no cluster.
        from PIL import Image
        pg.goto(login_url(base_url, ui_token)); pg.wait_for_selector("#flow rect.blk"); pg.wait_for_timeout(1200)
        pg.evaluate(f"setTheme('{SCHEMES[SCHEME]}')"); pg.evaluate("setView(true, false)"); pg.evaluate("setTree(false)"); pg.evaluate(FLOW); pg.wait_for_timeout(500)
        # the diagram and the side panel (focus, blast radius, client's eye), side by side as on the real page; the caption has its own strip below
        def union():
            a, c = pg.locator("#trafficpanel").bounding_box(), pg.locator("#inspector").bounding_box()
            x, y = min(a["x"], c["x"]), min(a["y"], c["y"]); return {"x": x, "y": y, "width": max(a["x"] + a["width"], c["x"] + c["width"]) - x, "height": max(a["y"] + a["height"], c["y"] + c["height"]) - y}
        pg.click("#lens [data-lens=client]"); pg.wait_for_timeout(400); box = union(); pg.click("#lens [data-lens=all]"); pg.wait_for_timeout(300)
        strip = 46
        clip = {**box, "height": box["height"] + strip}; frames = []
        pg.evaluate("""([top, left, w, light]) => { const [bg, fg, bd] = light ? ['#f4f7ff', '#14213d', '#2f55d4'] : ['#0b1020', '#e8eefc', '#5b7cff']; const c = document.createElement('div'); c.id = 'democap'; c.setAttribute('aria-hidden', 'true');
            c.style.cssText = `position:absolute;left:${left}px;top:${top}px;width:${w}px;box-sizing:border-box;padding:8px 14px;border-radius:8px;font:600 18px system-ui,sans-serif;background:${bg};color:${fg};border:1px solid ${bd};pointer-events:none;z-index:99`;
            document.body.appendChild(c); }""", [box["y"] + box["height"] + 6, box["x"], box["width"], SCHEME == "light"])
        def traffic(to, n, err=None):
            e = f", err: {err}" if err is not None else ""
            pg.evaluate(f"onFlow({{level: 0, cursor: {len(frames) + 300}, events: [{{from: 'clients', to: '{to}', n: {n}, label: 'requests', nodes: []{e}}}], totals: flowSnap.totals, errors: flowSnap.errors, reachable: flowSnap.reachable}})")
        def fault():
            pg.evaluate(f"onFlow({{level: 0, cursor: {len(frames) + 300}, events: [{{from: 'injector', to: 'bmc-farm', n: 1, label: 'overheat on cn003', nodes: ['cn003']}}], totals: flowSnap.totals, errors: flowSnap.errors, reachable: flowSnap.reachable}})")
        def scene(caption, count, act=None, at=None):
            pg.evaluate("t => { document.getElementById('democap').textContent = t }", caption)
            for i in range(count):
                if act and i in (at or {0}): act()
                frames.append(Image.open(io.BytesIO(pg.screenshot(clip=clip, full_page=True))).convert("RGB")); pg.wait_for_timeout(70)
        scene("Clients reach every part; dots are real requests", 12, lambda: traffic("teleport", 2), {1, 5, 9})
        scene("Error answers are triangles (counted by the mocks)", 10, lambda: traffic("bmc-farm", 3, 2), {1, 5})
        scene("A fault: dashed parts are affected (declared, not measured)", 14, fault, {1})
        pg.evaluate("setBlast(null)")
        pg.click("#lens [data-lens=management]"); scene("Lens: one plane at a time, nothing moves", 8)
        pg.click("#lens [data-lens=client]"); scene("Client's eye: what a client should call and what it got", 10)
        pg.click("#lens [data-lens=all]")
        pg.evaluate("focusOn('teleport')"); scene("Focus: a part and its direct neighbours", 8)
        pg.evaluate("focusOn(null)")
        pg.evaluate("setView(false, false)"); scene("Flat view: the same diagram, calm", 7)
        pg.evaluate("setView(true, false)")
        pg.evaluate("setLevel('overview', false)"); scene("Global view: one card per cluster, double-click to dive in", 10)
        pg.evaluate("setLevel('full', false)")
        w = 820; frames = [f.resize((w, round(f.height * w / f.width)), Image.LANCZOS).quantize(64) for f in frames]
        frames[0].save(OUT / f"architecture-demo-{SCHEME}.gif", save_all=True, append_images=frames[1:], duration=150, loop=0, optimize=True)
        # the rest of the manual's web screenshots: the phone layout, the node panel, a running scenario, the stopped stack and the help
        from benchctl import scenario as scn
        def sample_page(width, height, cl=None, flow=True):
            q = b.new_page(viewport={"width": width, "height": height}, color_scheme=SCHEME)
            q.add_init_script("window.__WB_TEST = true; try { localStorage.setItem('workbench-theme', '" + SCHEMES[SCHEME] + "'); sessionStorage.setItem('workbench-tab', 'nodes'); } catch (e) {}")
            q.route("**/api/cluster", lambda r: r.fulfill(json=cl or cluster)); q.route("**/api/flow*", lambda r: r.abort())
            q.route("**/api/connections", lambda r: r.fulfill(json={"entities": connections.entities(SIM)})); q.route("**/api/settings", settings)
            q.route("**/api/nodes/*", lambda r: r.fulfill(json={"ip": "10.141.0.2", "bmc_ip": "10.148.0.2", "text": "cn002: powered off, BMC reports DOWN"}) if r.request.method == "GET" else r.abort())
            q.goto(login_url(base_url, ui_token)); q.wait_for_selector("#flow[data-cv] .cv-box"); q.wait_for_timeout(1500)  # the circuit is the page's default layout
            if flow: q.evaluate(FLOW)
            q.wait_for_timeout(400)
            return q
        def save(path):  # a long phone page is a flat-colour image: keep the file small (budget 300 KB, tests/test_docs.py)
            im = Image.open(path).convert("RGB")
            if path.stat().st_size > 250 * 1024:
                im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)
        phone = sample_page(390, 800); phone.screenshot(path=str(OUT / f"web-phone-{SCHEME}.png"), full_page=True); save(OUT / f"web-phone-{SCHEME}.png"); phone.close()
        off = {**cluster, "nodes": [{**n, "state": "down", "label": "Powered off", "power": "Off", "temp": 18, "bcm": "DOWN"} if n["name"] == "cn002" else n for n in cluster["nodes"]]}
        pg2 = sample_page(1280, 900, off); pg2.evaluate("showNode('cn002')"); pg2.wait_for_selector("#nodepane:not([hidden])"); pg2.wait_for_timeout(600)
        pg2.screenshot(path=str(OUT / f"web-node-powered-off-{SCHEME}.png"))
        pg2.locator("#nodeacts-fault").locator("xpath=ancestor::div[contains(@class,'np-card')]").screenshot(path=str(OUT / f"web-fault-actions-{SCHEME}.png")); pg2.close()
        pv = scn.preview(scn.load("node-down", ROOT / "scenarios"))
        state = {"running": True, "name": "node-down", "speed": 1.0, "stopped": False, "ok": None, "trace": None, "expects": [],
                 "steps": [{"at": x["at"], "target": x["target"], "do": x["do"], "status": "ok" if i < 2 else "pending", "detail": ""} for i, x in enumerate(pv["steps"])]}
        pg3 = sample_page(1280, 900); pg3.route("**/api/scenarios/run", lambda r: r.fulfill(json=state)); pg3.click("#tb-run"); pg3.evaluate("poll()"); pg3.wait_for_selector("#timeline li"); pg3.wait_for_timeout(1500)
        pg3.screenshot(path=str(OUT / f"web-scenario-running-{SCHEME}.png")); pg3.close()
        down = {**cluster, "nodes": [], "services": {k: False for k in cluster["services"]}}
        pg4 = sample_page(1280, 700, down, flow=False); pg4.wait_for_timeout(800); pg4.screenshot(path=str(OUT / f"web-stack-down-{SCHEME}.png")); pg4.close()
        pg5 = b.new_page(viewport={"width": 1180, "height": 760}, color_scheme=SCHEME); pg5.goto(login_url(base_url, ui_token)); pg5.goto(base_url + "/help/manual/11-which-command"); pg5.wait_for_timeout(500)
        pg5.screenshot(path=str(OUT / f"web-help-{SCHEME}.png")); pg5.close()
        # the circuit's zoom and layers, as an animation for the README: Fleet, then in to the Cluster, one layer at a time, then a Pool
        zp = sample_page(1280, 900); zp.evaluate("zoomTo('fleet')"); zp.wait_for_timeout(900)
        zp.evaluate("document.querySelector('header.top').style.setProperty('position', 'static', 'important')")
        zp.evaluate("zoomTo('cluster')"); zp.wait_for_timeout(900)
        zb = zp.locator("#trafficpanel").bounding_box(); zclip = {**zb, "height": zb["height"] + 46}; zframes = []
        zp.evaluate("""([top, left, w, light]) => { const [bg, fg, bd] = light ? ['#f4f7ff', '#14213d', '#2f55d4'] : ['#0b1020', '#e8eefc', '#5b7cff']; const c = document.createElement('div'); c.id = 'zoomcap'; c.setAttribute('aria-hidden', 'true');
            c.style.cssText = `position:absolute;left:${left}px;top:${top}px;width:${w}px;box-sizing:border-box;padding:8px 14px;border-radius:8px;font:600 18px system-ui,sans-serif;background:${bg};color:${fg};border:1px solid ${bd};pointer-events:none;z-index:99`;
            document.body.appendChild(c); }""", [zb["y"] + zb["height"] + 6, zb["x"], zb["width"], SCHEME == "light"])
        def zscene(caption, count, act=None, settle=0):
            zp.evaluate("t => { document.getElementById('zoomcap').textContent = t }", caption)
            if act: act()
            zp.wait_for_timeout(settle)  # a scene that should show the arrival, not the move
            for _ in range(count):
                zframes.append(Image.open(io.BytesIO(zp.screenshot(clip=zclip, full_page=True))).convert("RGB")); zp.wait_for_timeout(80)
        zscene("Zoom out: the Fleet, one card per cluster", 9, lambda: zp.evaluate("zoomTo('fleet')"), settle=1000)
        zscene("Zoom in: the Cluster, every part and wire", 10, lambda: zp.evaluate("zoomTo('cluster')"))
        for layer, word in (("access", "Access"), ("management", "Management"), ("monitoring", "Monitoring")):
            zscene(f"Layers: only {word} stays lit, the rest fades", 7, lambda layer=layer: zp.click(f"#cvstack button[data-layer={layer}]"))
        zscene("Layers: back to all of them", 5, lambda: zp.click("#cvstack button[data-layer=all]"))
        zscene("Zoom in again: a Pool, one card per node", 11, lambda: zp.evaluate("zoomTo('pool', {pool: 'sg'})"))
        zscene("Zoom back out to the Cluster", 9, lambda: zp.evaluate("zoomTo('cluster')"))
        zw = 820; zframes = [f.resize((zw, round(f.height * zw / f.width)), Image.LANCZOS).quantize(64) for f in zframes]
        zframes[0].save(OUT / f"circuit-zoom-{SCHEME}.gif", save_all=True, append_images=zframes[1:], duration=150, loop=0, optimize=True)
        zp.close()
        b.close()
finally:
    proc.terminate()
