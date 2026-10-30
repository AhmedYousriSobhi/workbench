"""Workload dots (#74) against the live stack: counters in the real mocks drive real dots in a real browser."""
import json
import os
import time
import urllib.request
from pathlib import Path

import pytest
from helpers import wait_for
from test_cluster_real import api, ui  # noqa: F401  (the module-scoped live stack and UI process)

JIRA = "http://127.0.0.1:8080"


def hit_jira(n):
    for _ in range(n):
        urllib.request.urlopen(f"{JIRA}/rest/api/2/search?jql=project%3DOPS", timeout=5).read()


def read_flow(base, token, want, seconds=8):
    """Read the SSE feed until `want(snapshot)` is true; returns every snapshot seen."""
    r = urllib.request.Request(base + "/api/flow", headers={"Authorization": f"Bearer {token}"})
    seen, end = [], time.time() + seconds
    with urllib.request.urlopen(r, timeout=seconds + 2) as resp:
        while time.time() < end:
            line = resp.readline().decode()
            if not line.startswith("data: "):
                continue
            seen.append(json.loads(line[6:]))
            if want(seen):
                break
            hit_jira(3)
    return seen


def test_client_requests_and_fault_buttons_appear_as_events(ui):  # noqa: F811
    base, h, token = ui
    seen = read_flow(base, token, lambda s: any(e["to"] == "jira-mock" for x in s for e in x["events"]))
    jira = [e for x in seen for e in x["events"] if e["to"] == "jira-mock"]
    assert jira and jira[0]["from"] == "clients" and jira[0]["n"] >= 1
    assert seen[-1]["totals"]["jira-mock"] >= 3 and all(seen[-1]["reachable"].values())
    # the UI's own polling must not show up as traffic to the BMC or BCM
    assert not any(e["to"] in ("bmc-farm", "bcm-mock") and e["from"] == "clients" for x in seen for e in x["events"])
    api(base, h, "/api/nodes/cn001/overheat", "POST")
    r = urllib.request.Request(base + "/api/flow", headers={"Authorization": f"Bearer {token}"})
    api(base, h, "/api/nodes/cn001/clear", "POST")
    with urllib.request.urlopen(r, timeout=5) as resp:  # fresh stream starts "from now": nothing replayed
        first = json.loads(resp.readline().decode()[6:])
    assert first["events"] == []


def test_dots_move_in_a_real_browser_and_pause(ui, tmp_path):  # noqa: F811
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    base, _h, token = ui
    out = Path(os.environ.get("BENCHCTL_SHOT_DIR", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"{base}/login?token={token}")
        page.wait_for_selector("#fb-jira-mock.up")  # block turns green once the feed reports it reachable
        shot = {"done": False}

        def traffic_until_dot():
            hit_jira(4)
            if page.locator("#dots circle.pk").count() and not shot["done"]:
                page.screenshot(path=str(out / "traffic-dots.png"))
                shot["done"] = True
            return shot["done"]
        assert wait_for(traffic_until_dot, timeout=15), "no dot appeared after real requests"
        assert int(page.text_content("#fc-jira-mock").split()[0].replace(",", "")) >= 4
        assert "req/min" in page.text_content("#fs-jira-mock")
        # a fault button draws a diamond
        page.click(".node[data-node=cn001]")
        page.click("#nodeacts-fault .btn[data-action=fan-fail]")
        page.wait_for_selector("#dots rect.pk", timeout=8000)
        page.click("#nodeacts-fix .btn[data-action=clear]")
        page.keyboard.press("Escape")
        # pause: counters keep counting, no new dots
        page.click("#flowpause")
        assert page.get_attribute("#flowpause", "aria-pressed") == "true"
        page.wait_for_timeout(1500)
        for _ in range(3):
            hit_jira(5)
            page.wait_for_timeout(1000)
        assert page.locator("#dots circle.pk").count() == 0
        assert "paused by you" in page.inner_text("#flowstate")
        # leaving the tab closes the feed
        # a hidden page closes the live feed
        page.evaluate("Object.defineProperty(document, 'hidden', {value: true, configurable: true}); document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_timeout(500)
        assert page.evaluate("flowEs === null")
        assert errors == []
        browser.close()
