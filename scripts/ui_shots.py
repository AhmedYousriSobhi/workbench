"""Screenshot and sanity-check the Web UI in headless Chromium (needs: pip install -e '.[browser]' && playwright install chromium).

    .venv/bin/python scripts/ui_shots.py [OUTDIR]     # default /tmp/workbench-ui-shots

Starts `python -m webui` on a free port, captures every tab at desktop and phone width in light and dark, and reports
console errors and horizontal overflow. Prints the PNG paths; exit 1 if any check fails.
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from webui import blast, graph

SYNTH_CLUSTER = """lastCluster = JSON.stringify({logins: [{name: 'login', mode: 'both', port: 2222}, {name: 'login2', mode: 'teleport', port: null}], services: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'jira-real': true, 'gpu-exporter': true, 'teleport': true}, actions: [{id: 'power-off', label: 'Power off', gpu_only: false}, {id: 'power-on', label: 'Power on', gpu_only: false}, {id: 'overheat', label: 'Overheat', gpu_only: false}, {id: 'fan-fail', label: 'Fan failure', gpu_only: false}, {id: 'clear', label: 'Clear faults', gpu_only: false}],
                            nodes: ['cn001', 'cn002', 'cn003', 'cn004'].map(n => ({name: n, kind: 'compute', state: 'ok', label: 'Healthy', temp: 45, power: 'On', gpus: 0}))
                              .concat(['gpu001', 'gpu002'].map(n => ({name: n, kind: 'gpu', state: n === 'gpu002' ? 'fault' : 'ok', label: n === 'gpu002' ? 'GPU error' : 'Healthy', temp: 44, power: 'On', gpus: 8, gpus_busy: 4})))}); renderCluster(JSON.parse(lastCluster)); clearTimeout(clusterTimer); if (flowEs) flowEs.close(); flowSnap = {reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'jira-real': true, 'gpu-exporter': true, teleport: true}, totals: {'bmc-farm': 42, 'bcm-mock': 17, 'jira-mock': 9, 'jira-real': 0, 'gpu-exporter': 120, teleport: 5}, faults: 0}; recent = [{t: Date.now(), to: 'jira-mock', n: 4, fault: true}, {t: Date.now(), to: 'jira-real', n: 0, fault: true}, {t: Date.now(), to: 'bmc-farm', n: 6, fault: false}]; paintBlocks();"""  # six healthy nodes; stops the poll so the real (stopped) stack does not overwrite them
SYNTH_GRAPH = graph.build(
    {k: True for k in ("bmc-farm", "bcm-mock", "jira-mock", "jira-real", "gpu-exporter", "teleport")},
    [{"name": n, "kind": "compute"} for n in ("cn001", "cn002", "cn003", "cn004")] + [{"name": n, "kind": "gpu"} for n in ("gpu001", "gpu002")],
    [{"name": "login", "mode": "both"}, {"name": "login2", "mode": "teleport"}])
SYNTH_CLUSTER = SYNTH_CLUSTER.replace("actions: [", "graph: " + json.dumps(SYNTH_GRAPH) + ", blast: " + json.dumps(blast.payload()) + ", actions: [", 1)
VIEWPORTS = {"desktop": (1280, 900), "phone": (390, 800)}


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
            for scheme in ("light", "dark"):
                for vp, (w, h) in VIEWPORTS.items():
                    page = browser.new_page(viewport={"width": w, "height": h}, color_scheme=scheme)
                    page.on("console", lambda m, r=result: r["console_errors"].append(m.text) if m.type == "error" else None)
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
                    page.evaluate("setTree(true, false)")  # a 1280 px window starts with the list folded; the checks below look at the list
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
                    page.click("#tb-traffic")  # documented links, real or simulated, observed or configured
                    page.wait_for_selector("#tr-rows tr td code, #tr-rows tr td:nth-child(3)")
                    tpath = out / f"traffic-{vp}-{scheme}.png"
                    page.screenshot(path=str(tpath), full_page=True)
                    result["shots"].append(str(tpath))
                    if page.locator("#tr-rows tr").count() < 5:
                        result["a11y"].append("traffic tab lists fewer than 5 links")
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
                        result["a11y"].append("errors have no text equivalent in the feed and the service box (#314)")
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
                        page.click("#treetoggle")  # the node list folds away, the diagram keeps the width
                        if page.locator("#nodesmain:visible").count() != 0:
                            result["a11y"].append("the node list did not fold away")
                        page.click("#treetoggle")
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
                    page.click("#scnbtn")  # scenarios sheet: list, filter, play buttons
                    page.wait_for_selector("#list .btn")
                    spath = out / f"scenarios-{vp}-{scheme}.png"
                    page.screenshot(path=str(spath))
                    result["shots"].append(str(spath))
                    page.click("#scn-close")
                    if page.locator("#scndlg[open]").count():
                        result["a11y"].append("scenarios sheet did not close")
                    page.click("#planbtn")  # planner sheet: opens, estimates, reacts to a stepper press, shows the command
                    page.wait_for_function("document.querySelector('#pl-msg').textContent.length > 5")
                    page.click("[data-step=pl-cpu][data-d='-1']")
                    page.wait_for_function("document.querySelector('#pl-cmd').textContent.includes('--cpu-nodes') || document.querySelector('#pl-cmd').textContent.startsWith('Does not fit')")
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
                    if vp == "desktop" and page.locator("#nodeacts-fault .btn").count() < 3:
                        result["a11y"].append("node panel is missing its fault actions")
                    page.click("#node-back")
                    if not page.locator("#nodepane").is_hidden() or page.locator("#tree").is_hidden():
                        result["a11y"].append("node panel did not close")
                    page.close()
            for w, h, count in ((1280, 900, 6), (1920, 1080, 50)):  # the architecture is the page (#319): it fills the width, the page itself never scrolls, the rails scroll
                page = browser.new_page(viewport={"width": w, "height": h})
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
                        page.select_option("#lens", choice); page.wait_for_timeout(250)
                        li = page.evaluate("({dim: document.querySelectorAll('#flow [data-ldim]').length, eye: $('eyepane').hidden ? '' : $('eyepane').textContent, pos: [...document.querySelectorAll('#flow [data-ent]')].map(e => { const r = e.getBoundingClientRect(); return [e.getAttribute('data-ent'), Math.round(r.x), Math.round(r.y)]; })})")
                        if li["dim"] < 2 or li["pos"] != before:
                            result["a11y"].append(f"lens {choice} dimmed nothing or moved a part")
                        if choice == "client" and ("not measured" not in li["eye"] or "BMC farm" not in li["eye"] or "Verdict" not in li["eye"]):
                            result["a11y"].append("client's-eye lens did not list the services with a verdict and 'not measured' where nothing counts")
                    page.select_option("#lens", "all"); page.wait_for_timeout(200)
                    if page.evaluate("document.querySelectorAll('#flow [data-ldim]').length"):
                        result["a11y"].append("lens All planes left parts dimmed")
                    page.locator("#flow g[data-ent='login']").dblclick(); page.wait_for_timeout(200)  # focus again for the checks below
                    page.click("#vw-flat"); page.wait_for_timeout(200)  # the view switcher (#324): style and level change, the focus stays
                    if page.evaluate("focusId") != "login" or page.get_attribute("#trafficpanel", "data-view") != "flat" or page.get_attribute("#vw-flat", "aria-pressed") != "true":
                        result["a11y"].append("switching to the flat view lost the focus or did not change the style")
                    page.click("#vw-ov"); page.wait_for_timeout(300)
                    if not page.locator("#flow .ovcard").count() or page.get_attribute("#vw-ov", "aria-pressed") != "true":
                        result["a11y"].append("the Overview button of the view switcher did not open the overview")
                    page.click("#vw-iso"); page.wait_for_timeout(400)
                    if page.locator("#flow .nd").count() != 6 or page.get_attribute("#trafficpanel", "data-view") != "iso":
                        result["a11y"].append("the 2.5D button of the view switcher did not return to the diagram in 2.5D")
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
                page = browser.new_page(viewport={"width": w, "height": h})
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
