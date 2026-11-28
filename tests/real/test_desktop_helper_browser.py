"""The page-title token in headless Chromium with the page's real CSP (issue 983): a stand-in helper on 127.0.0.1:47800 that
hands a fake token to the page's origin only. Both switches on: the title gets the token; either off: no request reaches the
helper and the console stays clean. No cluster needed."""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
pytest.importorskip("playwright")
pytest.importorskip("uvicorn")
import uvicorn
from playwright.sync_api import sync_playwright

from webui.app import create_app, login_url

ROOT = Path(__file__).resolve().parents[2]
FAKE = "FAKEtoken_0123456789abcdef"
KEY = "try { localStorage.setItem('workbench-desktop-helper', '1'); } catch (e) {}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def no_stack(*args, **kw):
    raise OSError("no stack")


@pytest.fixture
def helper():
    """The stand-in helper: answers the preflight and GET /token for one allowed origin, and records every request."""
    seen, allowed = [], {}

    class H(BaseHTTPRequestHandler):
        def _cors(self):
            if self.headers.get("Origin") == allowed.get("origin"):
                self.send_header("Access-Control-Allow-Origin", allowed["origin"])
                self.send_header("Access-Control-Allow-Headers", "X-Scope-Token")

        def do_OPTIONS(self):
            seen.append(("OPTIONS", self.path, self.headers.get("Origin")))
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            seen.append(("GET", self.path, self.headers.get("Origin")))
            self.send_response(200 if self.path == "/token" else 404)
            self._cors()
            self.end_headers()
            if self.path == "/token":
                self.wfile.write(FAKE.encode())

        def log_message(self, *args):
            pass

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 47800), H)
    except OSError:
        pytest.skip("127.0.0.1:47800 is in use (a real desktop helper?): stop it to run this test")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield seen, allowed
    srv.shutdown()
    srv.server_close()


def serve(tmp_path, on: bool) -> tuple[str, uvicorn.Server]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app("tok", str(tmp_path / "g.sock"), tmp_path, ROOT / "sim.yaml", fetch=no_stack,
                                                      poster=no_stack, desktop_helper=on), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server


@pytest.mark.parametrize("switch, key, titled", [(True, True, True), (True, False, False), (False, True, False)],
                         ids=["both-on", "key-off", "switch-off"])
def test_the_title_carries_the_token_only_with_both_switches(tmp_path, helper, switch, key, titled):
    seen, allowed = helper
    base, server = serve(tmp_path, switch)
    allowed["origin"] = base
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()  # the real CSP: no bypass_csp
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            if key:
                page.add_init_script(KEY)
            page.goto(login_url(base, "tok"))
            if titled:
                page.wait_for_function(f"document.title.endsWith({FAKE!r})", timeout=5000)
            else:
                page.wait_for_timeout(1500)  # long enough for both fetches
            title = page.title()
            if switch and not key:  # the Connections line comes from the server while the switch is on
                status = page.evaluate("fetch('/api/connections').then(r => r.json()).then(j => j.entities.find(e => e.id === 'ui').status)")
                assert status.startswith("Desktop helper switch on")
            browser.close()
    finally:
        server.should_exit = True
    if titled:
        assert title.endswith(" " + FAKE) and title.count(FAKE) == 1
        assert ("OPTIONS", "/token", base) in seen and ("GET", "/token", base) in seen  # the preflight, then the token
    else:
        assert FAKE not in title and seen == []  # no request reached the helper
    assert not [e for e in errors if "47800" in e or "Content Security Policy" in e], errors  # nothing blocked, nothing logged
