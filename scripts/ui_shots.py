"""Screenshot and sanity-check the Web UI in headless Chromium (needs: pip install -e '.[browser]' && playwright install chromium).

    .venv/bin/python scripts/ui_shots.py [OUTDIR]     # default /tmp/workbench-ui-shots

Starts `python -m webui` on a free port, captures every tab at desktop and phone width in light and dark, and reports
console errors and horizontal overflow. Prints the PNG paths; exit 1 if any check fails.
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SYNTH_CLUSTER = """lastCluster = JSON.stringify({logins: [{name: 'login', mode: 'both', port: 2222}, {name: 'login2', mode: 'teleport', port: null}], services: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true, 'teleport': true}, actions: [{id: 'power-off', label: 'Power off', gpu_only: false}, {id: 'power-on', label: 'Power on', gpu_only: false}, {id: 'overheat', label: 'Overheat', gpu_only: false}, {id: 'fan-fail', label: 'Fan failure', gpu_only: false}, {id: 'clear', label: 'Clear faults', gpu_only: false}],
                            nodes: ['cn001', 'cn002', 'cn003', 'cn004'].map(n => ({name: n, kind: 'compute', state: 'ok', label: 'Healthy', temp: 45, power: 'On', gpus: 0}))
                              .concat(['gpu001', 'gpu002'].map(n => ({name: n, kind: 'gpu', state: 'ok', label: 'Healthy', temp: 44, power: 'On', gpus: 8})))}); renderCluster(JSON.parse(lastCluster)); clearTimeout(clusterTimer); if (flowEs) flowEs.close(); flowSnap = {reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true, teleport: true}, totals: {'bmc-farm': 42, 'bcm-mock': 17, 'jira-mock': 9, 'gpu-exporter': 120, teleport: 5}, faults: 0}; paintBlocks();"""  # six healthy nodes; stops the poll so the real (stopped) stack does not overwrite them
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
        url = proc.stdout.readline().split("UI: ")[1].strip()
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
                    page.evaluate("""onFlow({level: 0, cursor: 9, events: [{from: 'clients', to: 'bmc-farm', n: 3, label: 'requests', nodes: []},
                        {from: 'clients', to: 'jira-mock', n: 2, label: 'requests', nodes: []}, {from: 'clients', to: 'teleport', n: 2, label: 'requests', nodes: []}, {from: 'injector', to: 'bmc-farm', n: 1, label: 'x', nodes: ['cn002']}],
                        totals: {'bmc-farm': 3, 'bcm-mock': 0, 'jira-mock': 2, 'gpu-exporter': 0, teleport: 2}, reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true, teleport: true}})""")
                    page.wait_for_timeout(450)  # packets in flight
                    if not page.evaluate("flowPaused || reduced.matches") and page.locator("#dots .pk").count() < 1:
                        result["a11y"].append("traffic animation drew no packets for synthetic events")
                    tpath = out / f"architecture-{vp}-{scheme}.png"
                    page.locator("#trafficpanel").screenshot(path=str(tpath))
                    result["shots"].append(str(tpath))
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
                    page.click("#menubtn")
                    page.click("#scnbtn")  # scenarios sheet: list, filter, play buttons
                    page.wait_for_selector("#list .btn")
                    spath = out / f"scenarios-{vp}-{scheme}.png"
                    page.screenshot(path=str(spath))
                    result["shots"].append(str(spath))
                    page.click("#scn-close")
                    if page.locator("#scndlg[open]").count():
                        result["a11y"].append("scenarios sheet did not close")
                    page.click("#menubtn")
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
