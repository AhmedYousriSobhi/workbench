"""The Health checks sheet in headless Chromium: drag to reorder, keyboard buttons, toggle, add from a template, a refusal (#170)."""
import shutil
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")
import uvicorn
from playwright.sync_api import sync_playwright

from webui.app import create_app

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def no_stack(*args, **kw):
    raise OSError("no stack")


def test_the_sheet_changes_the_files_through_drag_and_buttons(tmp_path):
    src = tmp_path / "nhc.d"
    shutil.copytree(ROOT / "images" / "node-base" / "nhc.d", src)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app("tok", str(tmp_path / "g.sock"), tmp_path, ROOT / "sim.yaml", fetch=no_stack, poster=no_stack,
                                                      nhc_src=src, nhc_out=tmp_path / "out"), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.05)

    def order():
        return [ln.split()[0] for ln in (src / "checks.conf").read_text().splitlines() if ln and not ln.startswith("#")]

    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for width in (1280, 390):
                page = browser.new_context(viewport={"width": width, "height": 800}).new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/login?token=tok")
                page.click("#menubtn")
                page.click("#nhcbtn")
                page.wait_for_selector(".nhcrow")
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")  # nothing scrolls sideways (W-9)
                if width == 390:
                    break
                page.locator('.nhcrow[data-name="nic-flap"]').drag_to(page.locator('.nhcrow[data-name="gpu-count"]'), target_position={"x": 40, "y": 2})
                page.wait_for_function("document.querySelector('.nhcrow').dataset.name === 'nic-flap'")
                assert order()[0] == "nic-flap"
                page.click('[aria-label="Move gpu-xid down"]')
                page.wait_for_function("document.activeElement && document.activeElement.getAttribute('aria-label') === 'Move gpu-xid up'")  # focus follows the row
                assert order().index("gpu-xid") == 3
                page.locator('.nhcrow[data-name="ib-ports"] .tog').click()
                page.wait_for_function("document.querySelector('[data-name=ib-ports]').classList.contains('off')")
                assert "ib-ports off" in (src / "checks.conf").read_text()
                page.click('.nhcchip[data-tpl="metric-above"]')
                page.fill("#nhc-new-name", "gpu-hot")
                page.fill("[data-newparam=metric]", "DCGM_FI_DEV_GPU_TEMP")
                page.fill("[data-newparam=max]", "85")
                page.click("#nhc-new-go")
                page.wait_for_selector('.nhcrow[data-name="gpu-hot"]')
                assert order()[-1] == "gpu-hot"
                page.fill('[aria-label="gpu-hot max"]', "bad value!")
                page.locator('[aria-label="gpu-hot max"]').blur()
                page.wait_for_function("document.getElementById('nhc-msg').textContent.includes('nothing changed')")
                assert page.input_value('[aria-label="gpu-hot max"]') == "85"  # the rejected edit does not linger
            browser.close()
    finally:
        server.should_exit = True
    assert errors == []
