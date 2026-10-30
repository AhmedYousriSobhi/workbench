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
                    for sel in ("#banner", "#attn", "#list .btn", "#services .cell", "#flow", ".node"):
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
                        result["a11y"].append("traffic diagram is missing the node tiles or the clients")
                    page.evaluate("""onFlow({level: 0, cursor: 9, events: [{from: 'clients', to: 'bmc-farm', n: 3, label: 'requests', nodes: []},
                        {from: 'clients', to: 'jira-mock', n: 2, label: 'requests', nodes: []}, {from: 'injector', to: 'bmc-farm', n: 1, label: 'x', nodes: ['cn002']}],
                        totals: {'bmc-farm': 3, 'bcm-mock': 0, 'jira-mock': 2, 'gpu-exporter': 0}, reachable: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true}})""")
                    page.wait_for_timeout(450)  # packets in flight
                    if not page.evaluate("flowPaused || reduced.matches") and page.locator("#dots .pk").count() < 1:
                        result["a11y"].append("traffic animation drew no packets for synthetic events")
                    tpath = out / f"traffic-{vp}-{scheme}.png"
                    page.locator("#trafficpanel").screenshot(path=str(tpath))
                    result["shots"].append(str(tpath))
                    if vp == "desktop":  # one screen: with six nodes drawn, traffic and tree both fit without page scrolling
                        page.evaluate("""lastCluster = JSON.stringify({services: {'bmc-farm': true, 'bcm-mock': true, 'jira-mock': true, 'gpu-exporter': true}, actions: [],
                            nodes: ['cn001', 'cn002', 'cn003', 'cn004'].map(n => ({name: n, kind: 'compute', state: 'ok', label: 'Healthy', temp: 45, power: 'On', gpus: 0}))
                              .concat(['gpu001', 'gpu002'].map(n => ({name: n, kind: 'gpu', state: 'ok', label: 'Healthy', temp: 44, power: 'On', gpus: 8})))}); renderCluster(JSON.parse(lastCluster));""")
                        page.wait_for_timeout(300)
                        if page.evaluate("document.documentElement.scrollHeight > window.innerHeight + 2"):
                            result["a11y"].append("dashboard needs page scrolling at desktop size")
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
                    page.evaluate("showNode('cn001')")  # node Details dialog (its button is hidden while the stack is down): text, fits, closes
                    page.wait_for_selector("#nodedlg[open]")
                    page.wait_for_function("document.querySelector('#nodetext').textContent.includes('Name:')")
                    dpath = out / f"node-details-{vp}-{scheme}.png"
                    page.screenshot(path=str(dpath))
                    result["shots"].append(str(dpath))
                    box = page.locator("#nodedlg").bounding_box()
                    if box["x"] < 0 or box["x"] + box["width"] > w:
                        result["overflow"].append(f"node details {vp} {scheme}")
                    page.click("#nodedlg form .btn")
                    if page.locator("#nodedlg[open]").count():
                        result["a11y"].append("node details dialog did not close")
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
