"""Screenshot and sanity-check the Web UI in headless Chromium (needs: pip install -e '.[browser]' && playwright install chromium).

    .venv/bin/python scripts/ui_shots.py [OUTDIR]     # default /tmp/workbench-ui-shots

Starts `python -m webui` on a free port, captures every tab at desktop and phone width in light and dark, and reports
console errors and horizontal overflow. Prints the PNG paths; exit 1 if any check fails.
"""
import contextlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from webui import blast, graph

SYNTH_CLUSTER = """lastCluster = JSON.stringify({logins: [{name: 'login', mode: 'both', port: 2222}, {name: 'login2', mode: 'teleport', port: null}], services: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'jira-real': true, 'gpu-exporter': true, 'dcgm-exporter': true, 'real-gpu': true, 'teleport': true, 'jump': true, 'controller': true}, actions: [{id: 'power-off', label: 'Power off', gpu_only: false}, {id: 'power-on', label: 'Power on', gpu_only: false}, {id: 'overheat', label: 'Overheat', gpu_only: false}, {id: 'fan-fail', label: 'Fan failure', gpu_only: false}, {id: 'clear', label: 'Clear faults', gpu_only: false}],
                            nodes: ['cn001', 'cn002', 'cn003', 'cn004'].map(n => ({name: n, kind: 'compute', state: 'ok', label: 'Healthy', temp: 45, power: 'On', gpus: 0}))
                              .concat(['gpu001', 'gpu002'].map(n => ({name: n, kind: 'gpu', state: n === 'gpu002' ? 'fault' : 'ok', label: n === 'gpu002' ? 'GPU error' : 'Healthy', temp: 44, power: 'On', gpus: 8, gpus_busy: 4}))).concat([{name: 'rgpu001', kind: 'real-gpu', state: 'ok', label: 'Healthy', temp: 55, power: 'On', gpus: 1, gpus_busy: 1}])}); renderCluster(JSON.parse(lastCluster)); clearTimeout(clusterTimer); if (flowEs) flowEs.close(); flowSnap = {reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'jira-real': true, 'gpu-exporter': true, 'dcgm-exporter': true, 'real-gpu': true, teleport: true}, totals: {'bmc-farm': 42, 'bcm-mock': 17, 'jira-mock': 9, 'jira-real': 0, 'gpu-exporter': 120, teleport: 5}, faults: 0}; recent = [{t: Date.now(), to: 'jira-mock', n: 4, fault: true}, {t: Date.now(), to: 'jira-real', n: 0, fault: true}, {t: Date.now(), to: 'bmc-farm', n: 6, fault: false}]; paintBlocks();"""  # six healthy nodes; stops the poll so the real (stopped) stack does not overwrite them
SYNTH_GRAPH = graph.build(
    {k: True for k in ("bmc-farm", "bcm-mock", "jira-mock", "jira-real", "gpu-exporter", "dcgm-exporter", "real-gpu", "teleport", "jump", "controller")},
    [{"name": n, "kind": "compute"} for n in ("cn001", "cn002", "cn003", "cn004")] + [{"name": n, "kind": "gpu"} for n in ("gpu001", "gpu002")],
    [{"name": "login", "mode": "both"}, {"name": "login2", "mode": "teleport"}])
SYNTH_CLUSTER = SYNTH_CLUSTER.replace("actions: [", "graph: " + json.dumps(SYNTH_GRAPH) + ", blast: " + json.dumps(blast.payload()) + ", actions: [", 1)
TEST_HOOK = "window.__WB_TEST = true; try { localStorage.setItem('workbench-circuit', '0'); sessionStorage.getItem('workbench-tab') || sessionStorage.setItem('workbench-tab', 'nodes'); } catch (e) {}"  # (the classic diagram: the circuit is the default and has its own checks) main.js then exposes its modules as globals (webui/static/js/testhook.js) so the checks below can call them by name
VIEWPORTS = {"desktop": (1280, 900), "phone": (390, 800)}


WIRE_PROBE = """async () => {
  const w = await import('/static/js/wire.js');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.id = 'wirecheck'; svg.setAttribute('width', '100'); svg.setAttribute('height', '16');
  svg.append(w.buildWire({ d: 'M0 8 L90 8', plane: 'access', rpm: 12, errors: 2, label: 'probe' }), w.buildWire({ d: 'M0 8 L90 8', plane: 'data' }));
  document.body.append(svg);
  const look = sel => { const e = svg.querySelector(sel); if (!e) return null; const c = getComputedStyle(e); return { name: c.animationName, state: c.animationPlayState, dash: c.strokeDasharray, dur: c.animationDuration }; };
  return { pulse: look('.wire-pulse'), err: look('.wire-err'), idle: look('.wire-idle'), title: svg.querySelector('title').textContent, state: svg.querySelector('.wire').dataset.state,
           layers: svg.querySelectorAll('.wire')[0].children.length, idleLayers: svg.querySelectorAll('.wire')[1].children.length };
}"""
WIRE_PAUSE = """async () => {
  const pulse = () => getComputedStyle(document.querySelector('#wirecheck .wire-pulse')).animationPlayState;
  const before = pulse();
  Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
  const hidden = { state: pulse(), flag: document.documentElement.hasAttribute('data-tab-hidden') };
  delete document.hidden;
  document.dispatchEvent(new Event('visibilitychange'));
  return { before, hidden, after: pulse(), flagAfter: document.documentElement.hasAttribute('data-tab-hidden') };
}"""


def check_wire(page) -> list[str]:
    """The shared wire (webui/static/js/wire.js, canvas.css): four strokes and a red dash, a text alternative, motion that stops
    under reduced motion and while the tab is hidden, and the self-hosted fonts that really load. Returns the problems found."""
    bad = []
    r = page.evaluate(WIRE_PROBE)
    if r["layers"] != 6 or r["idleLayers"] != 3:  # title + 4 strokes + error dash; title + base + idle stroke
        bad.append(f"unexpected layers {r['layers']}/{r['idleLayers']}")
    if r["state"] != "errors" or "probe: Access wire, 12 requests a minute, 2 errors" != r["title"]:
        bad.append(f"no text alternative: {r['title']!r} / {r['state']}")
    if not page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"):
        for k in ("pulse", "err"):
            if r[k]["name"] != "wire-flow" or r[k]["state"] != "running":
                bad.append(f"{k} does not animate: {r[k]}")
        if r["pulse"]["dash"].replace("px", "").replace(",", "") != "2 18" or r["err"]["dash"].replace("px", "").replace(",", "") != "1 60":
            bad.append(f"dash patterns changed: {r['pulse']['dash']} / {r['err']['dash']}")
        p = page.evaluate(WIRE_PAUSE)
        if p["before"] != "running" or p["hidden"] != {"state": "paused", "flag": True} or p["after"] != "running" or p["flagAfter"]:
            bad.append(f"hidden tab does not pause the wires: {p}")
        page.emulate_media(reduced_motion="reduce")
        names = page.evaluate("[...document.querySelectorAll('#wirecheck .wire-pulse, #wirecheck .wire-err')].map(e => getComputedStyle(e).animationName)")
        page.emulate_media(reduced_motion="no-preference")
        if names != ["none", "none"]:
            bad.append(f"reduced motion leaves animation on: {names}")
    if r["idle"]["name"] != "none":
        bad.append("an idle wire animates")
    fonts = page.evaluate("Promise.all([document.fonts.load('600 16px Inter'), document.fonts.load('400 13px \"JetBrains Mono\"')]).then(f => f.map(x => x.length))")
    if fonts != [1, 1]:
        bad.append(f"fonts did not load from /static/fonts: {fonts}")
    page.evaluate("document.getElementById('wirecheck').remove()")
    return bad


def check_circuit(page) -> list[str]:
    """The Cluster level (webui/static/js/circuit.js): the same parts and wires whatever the cluster has, absent parts greyed, one
    layer isolated at a time, a text alternative on every wire. Leaves the page on the view it found. Returns the problems found."""
    bad = []
    page.click("#vw-cv")
    page.wait_for_selector("#flow[data-cv] .cv-box")
    r = page.evaluate("""() => { const q = s => [...document.querySelectorAll(s)], wires = q('#flow g.wire');
        return { wires: wires.length, untitled: wires.filter(w => !w.querySelector('title')?.textContent).length, parts: q('#flow [data-part]').length, layers: q('#cvstack button[data-layer]').length,
                 pressed: q('#cvstack button[aria-pressed=true]').length, bands: q('#flow .cv-band').length,
                 greyed: q('#flow [data-part][data-present=false]').every(e => +getComputedStyle(e).opacity <= 0.1), noteOnAbsent: q('#flow .cv-band.absent').every(b => b.querySelector('.cv-note')) } }""")
    if r["wires"] < 25 or r["untitled"] or r["parts"] < 20 or r["layers"] != 6 or r["pressed"] != 1 or r["bands"] != 2:
        bad.append(f"unexpected circuit: {r}")
    if not r["greyed"] or not r["noteOnAbsent"]:
        bad.append(f"an absent part is not greyed with a note: {r}")
    page.click("#cvstack button[data-layer=access]")
    page.wait_for_timeout(500)  # wires fade over 0.35 s
    f = page.evaluate("""() => { const w = [...document.querySelectorAll('#flow g.wire')]; return { faded: w.filter(x => x.dataset.faded === '1').length, lit: w.filter(x => x.dataset.faded === '0' && x.dataset.layer !== 'access').length,
        opacity: w.filter(x => x.dataset.faded === '1').every(x => +getComputedStyle(x).opacity <= 0.11) } }""")
    if not f["faded"] or f["lit"] or not f["opacity"]:
        bad.append(f"the Access layer does not isolate its wires: {f}")
    page.click("#cvstack button[data-layer=all]")
    bad += check_zoom(page)
    page.click("#vw-iso")
    if page.evaluate("document.getElementById('flow').dataset.cv") is not None:
        bad.append("leaving the circuit left it on")
    return bad


def check_zoom(page) -> list[str]:
    """The zoom (webui/static/js/zoom.js): Cluster to Pool to Fleet and back. After each move exactly one layer is shown and the others
    are out of layout and inert, so a level never stays visible or clickable behind the one that replaced it. Returns the problems found."""
    bad = []
    probe = """() => Object.fromEntries([['fleet', '#fleetview'], ['cluster', '#trafficpanel > .flowscroll'], ['pool', '#poolview']].map(([k, s]) => {
        const e = document.querySelector(s); return [k, e.hidden || getComputedStyle(e).display === 'none' ? (e.inert ? 'gone' : 'hidden but not inert') : 'shown']; }))"""

    def only(level, step):
        page.wait_for_timeout(1100)  # the move takes 0.7 s
        got = page.evaluate(probe)
        if any((v == "shown") != (k == level) or (v != "shown" and v != "gone") for k, v in got.items()):
            bad.append(f"after {step} only the {level} layer should show: {got}")

    if not page.locator("#flow .cv-rack .hit").count():
        return bad  # a cluster with no nodes has no pool to open
    page.locator("#flow .cv-rack .hit").first.dispatch_event("click")
    only("pool", "opening a rack")
    if not page.locator(".pv-tile").count() or not page.locator(".pv-filters button").count():
        bad.append("the pool level drew no tiles or filters")
    page.keyboard.press("Escape")
    only("cluster", "Escape in the pool")
    page.click("#zoomrail [data-level=fleet]")
    only("fleet", "the FLEET stop")
    page.click(".fl-card .fl-head")
    only("cluster", "opening a card")
    return bad


_POLL_CHECKED = False


def check_run(page, shot: str) -> list[str]:
    """Test run (webui/static/js/testrun.js): the library, a chosen scenario's timeline before Play with the CLI-only steps marked in
    words (U7), the Client's eye panel, and no sideways scroll. Returns the problems found."""
    bad = []
    page.click("#tb-run")
    page.wait_for_selector("#list .scnsel", state="visible")
    if page.locator("#tab-nodes").is_visible() or page.locator("#scndlg").count():
        bad.append("Test run should be the only workspace shown and the scenarios drawer should be gone")
    names = page.locator("#list .scnsel").evaluate_all("els => els.map(e => e.dataset.name)")
    for name in names:  # the preview says what is CLI-only before anything runs, and says it in words
        page.click(f'#list .scnsel[data-name="{name}"]')
        page.wait_for_function("n => document.querySelector('#runtitle').textContent.startsWith(n) && document.querySelectorAll('#timeline li').length > 0", arg=name)
        tags, notice = page.locator("#timeline .clitag").count(), page.locator("#runcli").is_visible()
        cli_exp = "(CLI only)" in page.locator("#expects").inner_text()
        if bool(tags or cli_exp) != notice:
            bad.append(f"{name}: the CLI-only notice and the CLI-only marks disagree")
        if tags and "CLI only" not in page.locator("#timeline .clitag").first.text_content():
            bad.append(f"{name}: a CLI-only mark without words")
        if page.locator("#run-export").is_visible():
            bad.append(f"{name}: export offered before any run")
    if not page.locator("#run-eye").inner_text().strip():
        bad.append("Client's eye panel is empty")
    if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"):
        wide = page.evaluate("[...document.querySelectorAll('#tab-run *')].filter(e => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1).slice(0, 3).map(e => e.tagName + '#' + e.id + '.' + e.className)")
        bad.append(f"Test run scrolls sideways ({wide})")
    page.screenshot(path=shot)
    page.click("#tb-nodes")
    return bad


def check_hidden(page) -> list[str]:
    """W-11 (U1, U2): a hidden tab closes the governor feed and stops polling the cluster; showing it again resumes both."""
    bad = []
    page.evaluate("(() => { window.__reqs = 0; const f = window.fetch.bind(window); window.fetch = (...a) => { if (String(a[0]).includes('/api/cluster')) window.__reqs++; return f(...a); }; })()")  # an expression that ends in a function would be called by evaluate
    page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(5200)
    if page.evaluate("window.__reqs") != 0:
        bad.append("the cluster was polled while the page was hidden")
    page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(1500)
    page.evaluate("delete document.hidden")
    if page.evaluate("window.__reqs") < 1:
        bad.append("showing the page did not resume cluster polling at once")
    return bad


def check_home(page) -> list[str]:
    """Overview (webui/static/js/home.js): one card per node with its state in words, the three start cards, services with real or
    simulated, and a node card opens that node in Topology. Returns the problems found."""
    bad = []
    page.click("#tb-home")
    page.wait_for_selector("#home-nodes .hn", state="visible")
    if page.locator("#tab-home").is_hidden() or page.locator("#tab-nodes").is_visible():
        bad.append("Overview should be the only workspace shown")
    if page.locator("#home-nodes .hn").count() != page.evaluate("lastCl.nodes.length"):
        bad.append("Overview shows a different number of node cards than the snapshot has nodes")
    if page.locator("#home-nodes .hn .pill").evaluate_all("els => els.some(e => !e.textContent.trim().replace(/[^A-Za-z]/g, ''))"):
        bad.append("a node card shows its state without words")
    if page.locator(".home-start .hcard").count() != 3 or not page.locator("#home-svcs .hs .tag").count():
        bad.append("Overview lacks the start cards or the services row")
    if page.locator("#home-attn-list li").count() < 1:
        bad.append("Needs attention shows neither an item nor the all-clear line")
    if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"):
        bad.append("Overview scrolls sideways")
    first = page.locator("#home-nodes .hn").first.get_attribute("data-open")
    page.locator("#home-nodes .hn").first.click()
    page.wait_for_selector("#nodepane", state="visible")
    if page.locator("#tab-home").is_visible() or first not in page.inner_text("#nodetitle"):
        bad.append("a node card did not open that node in Topology")
    page.click("#node-back")
    return bad


def check_analysis(page) -> list[str]:
    """The Traffic analysis screen (webui/static/js/analysis.js) with synthetic /api/traffic data: the river, the findings and the
    rhythm appear, a rate is not shown before two readings exist, a hash opens and clears the focused wire, a stale hash clears
    itself, and nothing polls while another tab is open (W-11). Leaves the page on the Traffic tab. Returns the problems found."""
    global _POLL_CHECKED
    bad, hits = [], {"n": 0}
    now = time.time()
    row = lambda who, svc, shape, n, se: {"who": who, "service": svc, "shape": shape, "n": n, "ok": n - se, "client_error": 0, "server_error": se, "ms": n * 4.0, "hist": [n, 0, 0, 0, 0, 0, 0, 0, 0]}
    recent = [[round(now - 300 + i * 5, 3), "unknown", "jira-mock", "GET /rest/api/2/serverInfo", 200, 3.0] for i in range(60)]
    recent += [[round(now - t, 3), "python-requests", "jira-mock", "GET /rest/api/2/search?jql", 503, 600.0] for t in (50, 120, 200)]
    body = {"now": round(now, 3), "edges": [5, 10, 25, 50, 100, 250, 500, 1000], "services": {"bmc-farm": True, "bcm-mock": True, "jira-mock": True},
            "streams": [row("unknown", "jira-mock", "GET /rest/api/2/serverInfo", 60, 0), row("python-requests", "jira-mock", "GET /rest/api/2/search?jql", 30, 3)], "recent": sorted(recent)}

    def answer(route):
        hits["n"] += 1
        route.fulfill(json=body)

    cap = {"starts": 0, "stops": 0, "reads": 0}

    def capture(route):
        url = route.request.url
        if url.endswith("/api/capture/start"):
            cap["starts"] += 1
            return route.fulfill(json={"ok": True, "node": "x", "seconds": 300})
        if url.endswith("/api/capture/stop"):
            cap["stops"] += 1
            return route.fulfill(json={"ok": True, "running": False})
        cap["reads"] += 1
        msgs = [{"t": now - 2, "dir": "in", "peer": "10.141.255.1", "peer_name": "slurmctld", "port": 6818, "size": 120, "type": "REQUEST_PING", "type_id": 1008, "kind": "request", "latency_ms": None, "version": 11520, "flags": 0, "body_length": 8},
                {"t": now - 1, "dir": "in", "peer": "10.141.0.9", "port": 6818, "size": 63, "type": "unknown", "type_id": None, "kind": "unknown", "latency_ms": None, "note": "the header does not parse"}]
        route.fulfill(json={"ok": True, "running": True, "node": "x", "messages": msgs if cap["reads"] == 1 else [], "next": 2, "ended": None, "error": None, "seconds_left": 290, "class": "P5"})

    page.route("**/api/capture/*", capture)
    page.route("**/api/traffic*", answer)
    page.evaluate("stopAnalysis(); startAnalysis()")
    try:
        page.wait_for_selector("#an-river .an-node", timeout=5000)
        page.wait_for_function("document.querySelectorAll('#an-found-list .an-f.no-retry, #an-found-list .an-f.timer').length >= 2", timeout=5000)
    except Exception:  # noqa: BLE001
        page.unroute("**/api/traffic*")
        page.unroute("**/api/capture/*")
        return ["the Traffic analysis screen did not draw the river and the findings from /api/traffic"]
    r = page.evaluate("""() => ({ status: document.getElementById('an-status').textContent, note: document.getElementById('an-river-note').textContent,
        nodes: document.querySelectorAll('#an-river .an-node').length, cols: document.querySelectorAll('.an-col').length, unk: document.querySelectorAll('.an-col.unk').length,
        rate: [...document.querySelectorAll('#an-river .an-ns')].some(n => /req\\/min/.test(n.textContent) && !/not measured/.test(n.textContent)) })""")
    if "recording" not in r["status"] or r["nodes"] != 9 or r["cols"] < 1 or r["unk"] < 1:
        bad.append(f"the analysis screen is incomplete: {r}")
    if r["rate"] or "Waiting for two readings" not in r["note"]:
        bad.append(f"a rate was shown before two readings existed: {r}")
    first = page.evaluate("clusterNow().nodes[0].name")
    page.evaluate(f"location.hash = {first!r}")
    page.wait_for_timeout(300)
    if not page.is_visible("#an-focus") or first not in page.inner_text("#an-focus"):
        bad.append("a node hash did not open the focused wire")
    try:
        page.wait_for_function("document.getElementById('an-capstate') && /Capturing/.test(document.getElementById('an-capstate').textContent) && document.querySelectorAll('#an-log tbody tr[data-i]').length >= 2", timeout=5000)
    except Exception:  # noqa: BLE001
        bad.append(f"the focused wire did not start a capture and show its messages: {page.inner_text('#an-capstate')!r}")
    if "unknown type" not in page.inner_text("#an-log") or page.locator(".an-cap").count() < 2 or "decoded" not in page.inner_text("#an-decoded").lower() or cap["starts"] != 1:
        bad.append(f"the capture panel is incomplete (an unknown type must say so in words; one capture started): starts={cap['starts']}")
    stops = cap["stops"]
    page.click("#an-clear")
    if cap["stops"] != stops + 1:
        bad.append("closing the focused wire did not stop the capture")
    if page.is_visible("#an-focus") or page.evaluate("location.hash") != "":
        bad.append("Clear focus did not clear the focus and the hash")
    page.evaluate("location.hash = 'no-such-node'")
    page.wait_for_timeout(200)
    if page.is_visible("#an-focus"):
        bad.append("a hash that names no node opened a focused wire")
    page.evaluate("history.replaceState(null, '', location.pathname + location.search)")
    if not _POLL_CHECKED:
        _POLL_CHECKED = True
        page.evaluate(f"location.hash = {first!r}")
        page.wait_for_function("document.getElementById('an-capstate') && /Capturing/.test(document.getElementById('an-capstate').textContent)", timeout=5000)
        stops = cap["stops"]
        page.click("#tb-nodes")
        before, reads = hits["n"], cap["reads"]
        page.wait_for_timeout(3500)  # a poll every 3 s (kept while visible, #538), a capture read every 2 s (stopped off the Traffic tab)
        if cap["reads"] != reads:
            bad.append("the wire capture kept reading while another tab was open (W-11)")
        if hits["n"] == before:
            bad.append("the traffic sampling stopped on another tab: it runs while the page is visible so a rate exists when Traffic opens (issue 538)")
        if cap["stops"] != stops + 1:
            bad.append("leaving the Traffic tab did not stop the capture")
        page.evaluate("history.replaceState(null, '', location.pathname + location.search)")
        page.click("#tb-traffic")
    page.unroute("**/api/traffic*")
    page.unroute("**/api/capture/*")
    page.evaluate("stopAnalysis(); startAnalysis()")
    return bad


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def capture(out: Path) -> dict:
    """{"shots": [paths], "console_errors": [...], "overflow": [...], "a11y": [...]}"""
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    result: dict = {"shots": [], "console_errors": [], "overflow": [], "a11y": []}
    try:
        url = proc.stdout.readline().split("UI: ")[1].strip() + "?token=" + (ROOT / "compose/generated/secrets/ui_token").read_text().strip()  # the log line has no token (#346)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            first = browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")  # nothing stored: dark first; the one page that keeps the CSP (#517)
            first.on("console", lambda m, r=result: r["a11y"].append("CSP violation: " + m.text[:160]) if "Content Security Policy" in m.text else None)
            first.goto(url)
            first.wait_for_timeout(500)
            for tab in first.locator("nav a, nav button, [role=tab]").all()[:12]:  # visit each screen so its templates run under the policy
                with contextlib.suppress(PlaywrightError):  # a tab that is hidden or covered is not what this loop checks
                    tab.click(timeout=1500)
                    first.wait_for_timeout(400)
            if first.evaluate("document.documentElement.dataset.theme") != "midnight":
                result["a11y"].append("a first visit does not start in the dark theme")
            first.close()
            for scheme in ("light", "dark"):
                for vp, (w, h) in VIEWPORTS.items():
                    page = browser.new_page(viewport={"width": w, "height": h}, color_scheme=scheme, bypass_csp=True)
                    page.add_init_script(TEST_HOOK)
                    page.add_init_script("localStorage.setItem('workbench-theme','auto')")  # the shots follow the system scheme; the default is checked above
                    page.on("console", lambda m, r=result: r["console_errors"].append(m.text + " " + m.location.get("url", "")) if m.type == "error" else None)
                    page.on("pageerror", lambda e, r=result: r["console_errors"].append(str(e)))
                    deadline = time.time() + 15
                    while True:
                        try:
                            page.goto(url)
                            break
                        except Exception:
                            if time.time() > deadline:
                                raise
                            time.sleep(0.3)
                    page.wait_for_timeout(2000)
                    page.wait_for_function("typeof setTree === 'function'", timeout=15000)  # main.js loads the test hook after its own imports
                    page.evaluate("setTree(true, false)")  # a 1280 px window starts with the list folded; the checks below look at the list
                    for problem in check_wire(page):
                        result["a11y"].append(f"wire component {vp} {scheme}: {problem}")
                    path = out / f"dashboard-{vp}-{scheme}.png"
                    page.screenshot(path=str(path), full_page=True)
                    result["shots"].append(str(path))
                    if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"):
                        result["overflow"].append(f"dashboard {vp} {scheme}")
                    for sel in ("#banner", "#attn", "#list .btn", "#services .er", "#flow", ".node"):
                        if page.locator(sel).count() == 0:
                            result["a11y"].append(f"dashboard is missing {sel}")
                    down = page.locator("#stackdown").is_visible()  # stack down: one notice, nothing repeating it
                    for sel in ("#svcpanel", "#br-compute"):
                        if page.locator(sel).is_visible() == down:
                            result["a11y"].append(f"{sel} must be {'hidden' if down else 'shown'} when the stack is {'down' if down else 'up'}")
                    page.keyboard.press("Tab")  # the skip link is the first stop
                    if not page.evaluate("document.activeElement.className === 'skip'"):
                        result["a11y"].append("skip link is not the first tab stop")
                    # landmarks and heading, theme toggle
                    if page.locator("main#main").count() != 1 or page.locator("header.top h1").count() != 1 or page.locator("aside").count() != 0:
                        result["a11y"].append("missing main landmark or page heading")
                    page.click("#theme")
                    if page.evaluate("document.documentElement.dataset.theme") != "light":
                        result["a11y"].append("theme toggle did not switch to light")
                    page.click("#theme")
                    if page.evaluate("document.documentElement.dataset.theme") != "dark":
                        result["a11y"].append("theme toggle did not switch to dark")
                    for problem in check_circuit(page):
                        result["a11y"].append(f"circuit {vp} {scheme}: {problem}")
                    for problem in check_home(page):
                        result["a11y"].append(f"overview {vp} {scheme}: {problem}")
                    hpath = out / f"overview-{vp}-{scheme}.png"
                    page.click("#tb-home")
                    page.screenshot(path=str(hpath), full_page=True)
                    result["shots"].append(str(hpath))
                    page.click("#tb-traffic")  # documented links, real or simulated, observed or configured
                    page.wait_for_selector("#tr-rows tr td:nth-child(3)", state="attached")
                    for problem in check_analysis(page):
                        result["a11y"].append(f"analysis {vp} {scheme}: {problem}")
                    tpath = out / f"traffic-{vp}-{scheme}.png"
                    page.screenshot(path=str(tpath), full_page=True)
                    result["shots"].append(str(tpath))
                    if page.locator("#tr-rows tr").count() < 5:
                        result["a11y"].append("traffic tab lists fewer than 5 links")
                    page.click("#tr-chips [data-plane] >> nth=1")  # a plane chip narrows the rows, All restores them
                    if page.locator("#tr-rows tr").count() >= page.evaluate("trRows.length") + 1:
                        result["a11y"].append("plane chip did not narrow the links")
                    page.click("#tr-chips [data-plane=all]")
                    page.click("#tr-zin")
                    if page.evaluate("trZoom") <= 1:
                        result["a11y"].append("zoom in did nothing")
                    page.click("#tr-zfit")
                    page.click("#tr-map [data-link] >> nth=0", force=True)  # inspector, then the path through that link
                    page.wait_for_selector("#tr-insp:not([hidden])")
                    page.click("#tr-follow")
                    page.wait_for_selector("#tr-path:not([hidden])")
                    if page.locator("#tr-rows tr").count() < 1:
                        result["a11y"].append("following a path left no links")
                    page.click("#tr-clear")
                    page.click("#tb-conn")  # connections tab: every entity listed with an address and a credential location
                    page.wait_for_selector("#conn-rows tr td code")
                    cpath = out / f"connections-{vp}-{scheme}.png"
                    page.screenshot(path=str(cpath), full_page=True)
                    result["shots"].append(str(cpath))
                    if page.locator("#conn-rows tr").count() < 8:
                        result["a11y"].append("connections tab lists fewer than 8 parts")
                    page.click("#tb-nodes")  # traffic sits beside the nodes: clients, services and node tiles in one diagram
                    page.wait_for_selector("#flow rect.blk")
                    if page.locator("#flow .nd").count() < 1 or page.locator("#flow text:has-text('Monitoring app')").count() != 1:
                        result["a11y"].append("Infrastructure architecture is missing the node tiles or the clients")
                    page.wait_for_timeout(450)  # the diagram fits itself to the panel once it has settled (#319); a rebuild would drop packets already in flight
                    page.evaluate("""onFlow({level: 0, cursor: 9, events: [{from: 'clients', to: 'bmc-farm', n: 3, err: 2, label: 'requests', nodes: []},
                        {from: 'clients', to: 'jira-mock', n: 2, label: 'requests', nodes: []}, {from: 'clients', to: 'teleport', n: 2, label: 'requests', nodes: []}, {from: 'injector', to: 'bmc-farm', n: 1, label: 'overheat on cn002', nodes: ['cn002']}],
                        errors: {'bmc-farm': 2}, totals: {'bmc-farm': 3, 'bcm-mock': 0, 'jira-mock': 2, 'gpu-exporter': 0, teleport: 2}, reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'jira-real': true, 'gpu-exporter': true, teleport: true}})""")
                    page.wait_for_timeout(450)  # packets in flight
                    if not page.evaluate("flowPaused || reduced.matches") and page.locator("#dots .pk").count() < 1:
                        result["a11y"].append("traffic animation drew no packets for synthetic events")
                    if not page.evaluate("flowPaused || reduced.matches") and page.locator("#dots polygon.pk.err").count() < 1:
                        result["a11y"].append("an error answer was not drawn as a triangle dot (#314)")
                    if "2 errors" not in page.text_content("#timeline-feed") or "2 errors" not in page.evaluate("document.getElementById('fc-bmc-farm').textContent"):
                        svc_text = page.evaluate("document.getElementById('fc-bmc-farm').textContent")
                        result["a11y"].append(f"errors have no text equivalent in the feed and the service box (#314): {page.text_content('#timeline-feed')[:80]!r} / {svc_text!r}")
                    b = page.evaluate("({marked: document.querySelectorAll('#flow [data-blast]').length, lanes: document.querySelectorAll('#flow path.lane[data-blast]').length, text: $('blastpane').hidden ? '' : $('blastpane').textContent})")
                    if b["marked"] < 3 or b["lanes"] < 1 or "declared" not in b["text"] or "BMC farm" not in b["text"]:
                        result["a11y"].append(f"blast radius did not mark the affected parts and say it is declared (#315): {b}")
                    drawn = set(page.evaluate("[...document.querySelectorAll('#flow [data-edge]')].map(e => e.dataset.edge)"))
                    declared = set(page.evaluate("JSON.parse(lastCluster).graph.edges.map(e => e.id)"))  # the model the page was given
                    if drawn != declared:  # the drawing and the declared graph must agree (#312)
                        result["a11y"].append(f"architecture draws {sorted(drawn ^ declared)} differently from the declared graph")
                    tpath = out / f"architecture-{vp}-{scheme}.png"
                    page.locator("#trafficpanel").screenshot(path=str(tpath))
                    result["shots"].append(str(tpath))
                    page.click("#keybtn")  # the key is a popover under the Key button: opens, shows the line styles, Escape closes it and gives the focus back
                    if page.locator("#ckey:visible").count() != 1 or page.get_attribute("#keybtn", "aria-expanded") != "true" or page.locator("#ckey .ls").count() != 4:
                        result["a11y"].append("the key did not open with its four line samples")
                    page.locator("#trafficpanel").screenshot(path=str(out / f"key-{vp}-{scheme}.png"))  # not in "shots": the count of reviewed screens stays
                    page.keyboard.press("Escape")
                    if page.locator("#ckey:visible").count() or page.evaluate("document.activeElement.id") != "keybtn":
                        result["a11y"].append("Escape did not close the key and return the focus to its button")
                    page.click("#lg-login")  # every part opens a pop-up with its details; Escape closes it and gives the focus back
                    if page.locator("#pop:visible dl").count() != 1:
                        result["a11y"].append("clicking a login node did not open its pop-up")
                    page.keyboard.press("Escape")
                    if page.locator("#pop:visible").count() != 0 or not page.evaluate("document.activeElement.id === 'lg-login'"):
                        result["a11y"].append("Escape did not close the pop-up and return the focus")
                    if vp == "desktop":
                        page.hover("#lg-login")
                        if page.locator("#tip:visible").count() != 1:
                            result["a11y"].append("hovering a part showed no tip")
                        page.mouse.move(2, 2)
                        page.evaluate(SYNTH_CLUSTER)  # with the stack down (a --full run, after the other modules tore theirs down) the list is hidden for want of nodes
                        page.wait_for_timeout(300)
                        page.click("[data-sub=topo]")  # the node list folds away, the diagram keeps the width
                        if page.locator("#nodesmain:visible").count() != 0:
                            result["a11y"].append("the node list did not fold away")
                        page.click("[data-sub=all]")
                        if page.locator("#nodesmain:visible").count() != 1:
                            result["a11y"].append("the node list did not come back")
                    if vp == "phone" and page.evaluate("document.querySelector('#nodeswrap').getBoundingClientRect().width < 300 || getComputedStyle(document.querySelector('#nodeswrap')).gridTemplateColumns.split(' ').length > 1"):
                        result["a11y"].append("phone layout must stack traffic and the list in one column")
                    if vp == "desktop":  # one screen: with six nodes drawn, traffic and tree both fit without page scrolling
                        page.evaluate(SYNTH_CLUSTER)
                        page.wait_for_timeout(300)
                        if page.evaluate("document.documentElement.scrollHeight > window.innerHeight + 2"):
                            result["a11y"].append("dashboard needs page scrolling at desktop size")
                    page.click("#menubtn")  # the top menu: opens as a disclosure, stays on screen, Escape closes it and returns focus
                    if page.get_attribute("#menubtn", "aria-expanded") != "true" or page.locator("#menu:visible").count() != 1:
                        result["a11y"].append("menu did not open")
                    mbox = page.locator("#menu").bounding_box() or {"x": -1, "y": -1, "width": 0, "height": 0}
                    if mbox["x"] < 0 or mbox["x"] + mbox["width"] > w + 1 or mbox["y"] + mbox["height"] > h + 1:
                        result["a11y"].append(f"menu does not fit the {vp} screen")
                    mpath = out / f"menu-{vp}-{scheme}.png"
                    page.screenshot(path=str(mpath))
                    result["shots"].append(str(mpath))
                    page.keyboard.press("Escape")
                    if page.locator("#menu:visible").count() or page.evaluate("document.activeElement.id") != "menubtn":
                        result["a11y"].append("Escape must close the menu and return focus to the Menu button")
                    spath = out / f"testrun-{vp}-{scheme}.png"
                    for problem in check_run(page, str(spath)):
                        result["a11y"].append(problem)
                    result["shots"].append(str(spath))
                    page.click("#planbtn")  # planner sheet: opens, estimates, reacts to a stepper press, shows the command
                    page.wait_for_function("document.querySelector('#pl-msg').textContent.length > 5")
                    page.click("[data-step=pl-cpu][data-d='-1']")
                    page.wait_for_function("document.querySelector('#pl-cmd').textContent.includes('--cpu-nodes') || document.querySelector('#pl-cmd').textContent.startsWith('Does not fit')")
                    page.wait_for_function("document.querySelector('#pl-review').textContent.includes('Removes')")  # 6d: the review names what Apply removes and restarts
                    rv = page.evaluate("({lines: document.querySelector('#pl-review').textContent, jobs: !document.querySelector('#pl-jobs').hidden, apply: document.querySelector('#pl-apply').disabled})")
                    if "cn004" not in rv["lines"] or "slurmctld" not in rv["lines"] or not rv["jobs"]:
                        result["a11y"].append(f"plan review ({vp} {scheme}): {rv}")
                    page.evaluate("planStates = {cn004: 'allocated'}; paintReview()")  # a removed node with jobs: the tick gates Apply
                    ak = page.evaluate("({row: !document.querySelector('#pl-ackrow').hidden, blocked: document.querySelector('#pl-apply').disabled, stop: document.querySelector('#pl-jobs').className.includes('stop')})")
                    page.check("#pl-ack")
                    if not (ak["row"] and ak["blocked"] and ak["stop"]) or page.evaluate("document.querySelector('#pl-apply').disabled"):
                        result["a11y"].append(f"plan review: running jobs must block Apply until ticked ({vp} {scheme}): {ak}")
                    page.evaluate("planStates = null; paintReview()")
                    page.evaluate("(() => { change = {job: 7, label: 'apply-plan', state: 'running', started: 1, finished: null, stages: [{name: 'Build images', state: 'done', secs: 12.4}, {name: 'Nodes', state: 'running', secs: null}, {name: 'Login node', state: 'waiting', secs: null}]}; paintProgress(7); })()")  # U5: the governor's stages, not the log text
                    pg = page.evaluate("({shown: !document.querySelector('#pl-prog').hidden, rows: [...document.querySelectorAll('#pl-stages li')].map(l => [...l.children].map(c => c.textContent).join(' ')), msg: document.querySelector('#pl-apply-msg').textContent})")
                    if not pg["shown"] or pg["rows"] != ["✓ Build images 12 s", "▶ Nodes running", "○ Login node waiting"] or "1 of 3 stages done" not in pg["msg"]:
                        result["a11y"].append(f"apply progress ({vp} {scheme}): {pg}")
                    ppath = out / f"plan-{vp}-{scheme}.png"
                    page.screenshot(path=str(ppath))
                    result["shots"].append(str(ppath))
                    box = page.locator("#plandlg").bounding_box()
                    if box["x"] < -1 or box["x"] + box["width"] > w + 1:
                        result["overflow"].append(f"plan sheet {vp} {scheme}")
                    page.click("#plan-close")
                    if page.locator("#plandlg[open]").count():
                        result["a11y"].append("plan sheet did not close")
                    page.click("#tb-nodes")
                    page.evaluate(SYNTH_CLUSTER)
                    page.evaluate("showNode('cn001')")  # node panel replaces the list in place: info and actions as two cards, traffic stays visible
                    page.wait_for_selector("#nodepane:not([hidden])")
                    page.wait_for_function("document.querySelector('#nodetext').textContent.includes('Name:')")
                    dpath = out / f"node-details-{vp}-{scheme}.png"
                    page.screenshot(path=str(dpath), full_page=True)
                    result["shots"].append(str(dpath))
                    if page.locator("dialog[open]").count() or not page.locator("#flow").is_visible() and vp == "desktop":
                        result["a11y"].append("node panel must not cover the Infrastructure architecture")
                    sm = page.evaluate("({keys: [...document.querySelectorAll('#nodesum dt')].map(e => e.textContent), reach: $('nodereach').hidden, chips: [...document.querySelectorAll('#lens [data-lens]')].map(b => b.getAttribute('aria-pressed'))})")  # 6c: summary first, a reach command, one pressed lens chip
                    if not {"Reason", "Hardware", "Power", "Address", "BMC"} <= set(sm["keys"]) or sm["reach"] or sm["chips"].count("true") != 1:
                        result["a11y"].append(f"node summary or lens chips: {sm}")
                    if vp == "desktop" and page.locator("#nodeacts-fault .btn").count() < 3:
                        result["a11y"].append("node panel is missing its fault actions")
                    page.click("#node-back")
                    if not page.locator("#nodepane").is_hidden() or page.locator("#tree").is_hidden():
                        result["a11y"].append(f"node panel did not close ({vp} {scheme}: panel hidden={page.locator('#nodepane').is_hidden()} tree hidden={page.locator('#tree').is_hidden()})")
                    if vp == "desktop" and scheme == "dark":
                        result["a11y"].extend(check_hidden(page))
                    page.close()
            for w, h, count in ((1280, 900, 6), (1920, 1080, 50)):  # the architecture is the page (#319): it fills the width, the page itself never scrolls, the rails scroll
                page = browser.new_page(viewport={"width": w, "height": h}, bypass_csp=True)
                page.add_init_script(TEST_HOOK)
                page.goto(url)
                page.wait_for_selector("#flow rect.blk")
                page.wait_for_timeout(1200)  # the first real poll has landed: it must not overwrite the sample
                page.evaluate(SYNTH_CLUSTER)
                page.evaluate("""n => { const c = JSON.parse(lastCluster); c.nodes = Array.from({length: n}, (_, i) => ({name: 'cn' + String(i + 1).padStart(3, '0'), kind: 'compute', state: 'ok', label: 'Healthy', temp: 45, power: 'On', gpus: 0}));
                    c.graph.entities = c.graph.entities.filter(e => !e.group).concat(c.nodes.map(x => ({id: x.name, type: 'compute-node', group: 'nodes'}))); lastCluster = JSON.stringify(c); renderCluster(c); buildFlow(); }""", count)
                page.wait_for_timeout(400)
                m = page.evaluate("""() => ({scroll: document.documentElement.scrollHeight - innerHeight, flow: document.querySelector('#flow').getBoundingClientRect().width,
                    list: document.querySelector('#nodesmain').getBoundingClientRect().height, insp: document.querySelector('#inspector').getBoundingClientRect().width,
                    fit: (() => { const f = document.querySelector('#flow'), v = f.viewBox.baseVal, r = f.getBoundingClientRect(); return (v.width / v.height) / (r.width / r.height); })()})""")
                if count == 6 and not 0.85 <= m["fit"] <= 1.18:  # the drawing is built from the panel's shape (#319): a ratio far from 1 means bars of empty space beside or above it
                    result["a11y"].append(f"the diagram does not fit its panel at {w}x{h}: drawing/panel aspect ratio {m['fit']:.2f}")
                if m["scroll"] > 2:
                    result["a11y"].append(f"dashboard scrolls the page at {w}x{h} with {count} nodes")
                if m["flow"] < w * (0.42 if w < 1500 else 0.48):
                    result["a11y"].append(f"the architecture is only {round(m['flow'])} px wide at {w}x{h}: the canvas should use the free space")
                if m["list"] < 120 or m["insp"] < 300:
                    result["a11y"].append(f"node list or inspector too small at {w}x{h}: {m}")
                if count == 6:  # focus view (#313): a part and its neighbours stay, the rest dims, nothing moves, Esc returns
                    rel0 = page.evaluate("[...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(), o = document.querySelector('#flow').getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x - o.x), Math.round(r.y - o.y)]; })")  # positions inside the diagram: clicking a chip may scroll the page, which is not a move
                    before = page.evaluate("[...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x), Math.round(r.y)]; })")
                    page.locator("#flow g[data-ent='login']").dblclick(); page.wait_for_timeout(300)
                    f = page.evaluate("({dim: document.querySelectorAll('#flow [data-dim]').length, crumb: !!document.querySelector('#crumb:not([hidden])'), rows: document.querySelectorAll('#focuspane tbody tr').length, faulted: document.querySelectorAll('#flow [data-dim] .nd.fault').length})")
                    after = page.evaluate("[...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x), Math.round(r.y)]; })")
                    if f["dim"] < 3 or not f["crumb"] or f["rows"] < 2 or f["faulted"]:
                        result["a11y"].append(f"focus view: {f}")
                    if before != after:
                        result["a11y"].append("focus view moved a part of the diagram")
                    page.keyboard.press("Escape"); page.wait_for_timeout(200)
                    for choice in ("management", "client"):  # lenses (#316): the layout never moves; the client's eye reads only real counters
                        page.click(f"#lens [data-lens={choice}]"); page.wait_for_timeout(250)
                        li = page.evaluate("({dim: document.querySelectorAll('#flow [data-ldim]').length, eye: $('eyepane').hidden ? '' : $('eyepane').textContent, pos: [...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(), o = document.querySelector('#flow').getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x - o.x), Math.round(r.y - o.y)]; })})")
                        if li["dim"] < 2 or li["pos"] != rel0:
                            result["a11y"].append(f"lens {choice} dimmed nothing or moved a part (dimmed {li['dim']}, moved {[(a, b) for a, b in zip(rel0, li['pos']) if a != b][:3]})")
                        if choice == "client" and ("not measured" not in li["eye"] or "BMC farm" not in li["eye"] or "Verdict" not in li["eye"]):
                            result["a11y"].append("client's-eye lens did not list the services with a verdict and 'not measured' where nothing counts")
                    page.click("#lens [data-lens=all]"); page.wait_for_timeout(200)
                    if page.evaluate("document.querySelectorAll('#flow [data-ldim]').length"):
                        result["a11y"].append("lens All planes left parts dimmed")
                    page.locator("#flow g[data-ent='login']").dblclick(); page.wait_for_timeout(200)  # focus again for the checks below
                    page.click("#vw-cv"); page.wait_for_timeout(300)  # the view switcher: the circuit and the classic diagram; the focus stays
                    if page.evaluate("focusId") != "login" or page.get_attribute("#vw-cv", "aria-pressed") != "true":
                        result["a11y"].append("switching to the circuit lost the focus or did not change the view")
                    page.click("#vw-iso"); page.wait_for_timeout(400)
                    if page.locator("#flow .nd").count() != 6 or page.get_attribute("#vw-iso", "aria-pressed") != "true":
                        result["a11y"].append("the classic button of the view switcher did not return to the 2.5D diagram")
                    page.locator("#flow g[data-ent='login']").dblclick(); page.wait_for_timeout(200)  # focus again for the Esc check
                    page.keyboard.press("Escape"); page.wait_for_timeout(200)
                    if page.evaluate("document.querySelectorAll('#flow [data-dim]').length") or page.evaluate("focusId"):
                        result["a11y"].append("Esc did not leave the focus view")
                level = page.evaluate("document.querySelector('#flow .ovcard') ? 'overview' : 'full'")  # the global view (#321): small clusters open at the full diagram, large ones at the overview
                if level != ("full" if count == 6 else "overview"):
                    result["a11y"].append(f"{count} nodes opened at the {level} level")
                if count == 50:
                    if not 0.9 <= m["fit"] <= 1.1:
                        result["a11y"].append(f"the overview does not fill its panel at {w}x{h}: ratio {m['fit']:.2f}")
                    page.hover("#flow .ovcard"); page.wait_for_timeout(150)
                    if not page.locator("#ovprev:visible").count():
                        result["a11y"].append("hovering the cluster card showed no preview of the design underneath")
                    page.mouse.move(2, 2); page.dblclick("#flow .ovcard"); page.wait_for_timeout(500)
                    if page.locator("#flow .nd").count() != 50 or page.locator("#crumb:visible").count() != 1:
                        result["a11y"].append("double-click did not open the full diagram with a breadcrumb")
                    page.keyboard.press("Escape"); page.wait_for_timeout(400)
                    if not page.locator("#flow .ovcard").count():
                        result["a11y"].append("Esc did not return to the overview")
                    page.focus("#flow .ovcard"); page.keyboard.press("Enter"); page.wait_for_timeout(400)
                    if page.locator("#flow .nd").count() != 50:
                        result["a11y"].append("Enter on the cluster card did not open the full diagram")
                    page.click("#crumb-all"); page.wait_for_timeout(300)
                    if not page.locator("#flow .ovcard").count() or not page.locator("#ovtable table").count():
                        result["a11y"].append("the breadcrumb did not return to the overview, or the cluster table is missing")
                page.close()
            for w, h, count in ((1280, 900, 2), (1280, 900, 12), (1920, 1080, 12)):  # several clusters (#327): a grid of cards that fits the panel; entering one shows that cluster
                page = browser.new_page(viewport={"width": w, "height": h}, bypass_csp=True)
                page.add_init_script(TEST_HOOK)
                page.goto(url)
                page.wait_for_selector("#flow rect.blk")
                page.wait_for_timeout(1200)
                page.evaluate(SYNTH_CLUSTER)
                page.evaluate("""n => { const c = JSON.parse(lastCluster), base = c.nodes;
                    c.clusters = Array.from({length: n}, (_, i) => ({name: 'cluster-' + (i + 1), nodes: base.slice(0, i === 1 ? 3 : base.length), logins: c.logins, services: c.services}));
                    lastCluster = JSON.stringify(c); renderCluster(c); setLevel('overview', false); }""", count)
                page.wait_for_timeout(400)
                m = page.evaluate("() => ({scroll: document.documentElement.scrollHeight - innerHeight, cards: document.querySelectorAll('#flow .ovcard').length, rows: document.querySelectorAll('#ovtable tbody tr').length, over: curLevel()})")
                if m["scroll"] > 2 or m["cards"] != count or m["rows"] != count or m["over"] != "overview":
                    result["a11y"].append(f"{count} clusters at {w}x{h}: {m}")
                page.locator("#flow .ovcard").nth(1).dblclick(); page.wait_for_timeout(500)
                m = page.evaluate("() => ({nodes: document.querySelectorAll('#flow .nd').length, crumb: document.querySelector('#crumb-name').textContent, list: document.querySelectorAll('#tree .er.node, #tree [data-node]').length})")
                if m["nodes"] != 3 or m["crumb"] != "cluster-2":
                    result["a11y"].append(f"entering cluster-2 of {count} at {w}x{h} did not show its own diagram: {m}")
                page.keyboard.press("Escape"); page.wait_for_timeout(300)
                if page.locator("#flow .ovcard").count() != count:
                    result["a11y"].append(f"Esc did not return to the {count}-cluster overview")
                page.close()
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return result


if __name__ == "__main__":
    res = capture(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/workbench-ui-shots"))
    print("\n".join(res["shots"]))
    for key in ("console_errors", "overflow", "a11y"):
        for item in res[key]:
            print(f"{key}: {item}")
    sys.exit(1 if any(res[k] for k in ("console_errors", "overflow", "a11y")) else 0)
