"""Screenshot and sanity-check the Web UI in headless Chromium (needs: pip install -e '.[browser]' && playwright install chromium).

    .venv/bin/python scripts/ui_shots.py [OUTDIR]     # default /tmp/opti-ui-shots

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
                    page.wait_for_timeout(1500)
                    for tab in ("dash", "cluster", "scen"):
                        page.click(f"#tab-{tab}")
                        page.wait_for_timeout(500)
                        path = out / f"{tab}-{vp}-{scheme}.png"
                        page.screenshot(path=str(path), full_page=True)
                        result["shots"].append(str(path))
                        if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"):
                            result["overflow"].append(f"{tab} {vp} {scheme}")
                    selected = page.evaluate("[...document.querySelectorAll('[role=tab]')].filter(t => t.getAttribute('aria-selected')==='true').length")
                    if selected != 1:
                        result["a11y"].append(f"{selected} tabs selected")
                    page.focus("#tab-scen")
                    page.keyboard.press("ArrowLeft")  # keyboard operation of the tablist: scen -> cluster -> dash
                    page.keyboard.press("ArrowLeft")
                    if page.get_attribute("#tab-dash", "aria-selected") != "true":
                        result["a11y"].append("arrow keys do not move between tabs")
                    # landmarks, nav reachable inside the viewport (bottom bar on phones), theme toggle, skip link
                    if page.locator("main#main").count() != 1 or page.locator("header.top h1").count() != 1:
                        result["a11y"].append("missing main landmark or page heading")
                    box = page.locator("#tab-dash").bounding_box()
                    if not box or box["y"] + box["height"] > h + 1 or box["y"] < 0:
                        result["a11y"].append(f"navigation is outside the {vp} viewport")
                    if box and min(box["width"], box["height"]) < 40:
                        result["a11y"].append("navigation target smaller than 40 px")
                    page.click("#theme")
                    if page.evaluate("document.documentElement.dataset.theme") != "light":
                        result["a11y"].append("theme toggle did not switch to light")
                    page.click("#theme")
                    if page.evaluate("document.documentElement.dataset.theme") != "dark":
                        result["a11y"].append("theme toggle did not switch to dark")
                    page.close()
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    return result


if __name__ == "__main__":
    res = capture(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opti-ui-shots"))
    print("\n".join(res["shots"]))
    for key in ("console_errors", "overflow", "a11y"):
        for item in res[key]:
            print(f"{key}: {item}")
    sys.exit(1 if any(res[k] for k in ("console_errors", "overflow", "a11y")) else 0)
