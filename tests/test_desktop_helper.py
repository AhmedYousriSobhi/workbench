"""The page-title token script and its CSP switch (issue 983): off by default, one loopback origin when enabled."""
import json
import re
import socket
import threading
import time
import urllib.request

import pytest
import uvicorn
from palette_lib import ROOT

from webui.app import create_app, csp, helper_on

STATIC = ROOT / "webui/static"
JS = (STATIC / "js/desktop-helper.js").read_text()
HTML = (STATIC / "index.html").read_text()
TOKEN = "t" * 24


def test_csp_is_unchanged_by_default():
    default = csp()
    assert "connect-src 'self';" in default and "127.0.0.1" not in default
    assert csp(False) == default


def test_csp_adds_exactly_one_loopback_origin_when_enabled():
    on = csp(True)
    assert "connect-src 'self' http://127.0.0.1:47800;" in on
    assert on.count("127.0.0.1") == 1 and "unsafe-inline" not in on
    assert on.replace(" http://127.0.0.1:47800", "") == csp()  # nothing else changes


@pytest.mark.parametrize("value, on", [(None, False), ("0", False), ("true", False), ("1", True)])
def test_only_the_value_1_turns_the_switch_on(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv("WORKBENCH_DESKTOP_HELPER", raising=False)
    else:
        monkeypatch.setenv("WORKBENCH_DESKTOP_HELPER", value)
    assert helper_on() is on


def test_script_is_opt_in_and_never_throws():
    assert "localStorage.getItem('workbench-desktop-helper') === '1'" in JS
    assert JS.index("if (!on) return;") < JS.index("fetch(")
    assert ".catch(" in JS and re.search(r"try \{[^}]*getItem", JS)
    assert "document.title = base + ' ' + t" in JS and "{16,}" in JS  # only a plausible token is appended


def test_script_asks_the_page_before_the_helper():
    """A page started without the switch blocks the loopback request (CSP) and the browser logs it: ask first, same origin."""
    assert JS.index("fetch('/api/desktop-helper')") < JS.index("http://127.0.0.1:47800/token")
    assert JS.index("if (!j.on) return '';") < JS.index("http://127.0.0.1:47800/token")


def test_script_is_a_plain_deferred_self_script():
    assert '<script src="/static/js/desktop-helper.js" defer></script>' in HTML
    assert "import " not in JS and "export " not in JS


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _no_stack(*args, **kw):
    raise OSError("no stack in unit tests")


@pytest.fixture
def page(tmp_path):
    """Starts the page with the switch given, returns a GET helper: (status, headers, body)."""
    servers = []

    def start(helper: bool):
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, str(tmp_path / "gov.sock"), tmp_path, ROOT / "sim.yaml", fetch=_no_stack,
                                                          poster=_no_stack, desktop_helper=helper), host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
        deadline = time.time() + 5
        while not server.started and time.time() < deadline:
            time.sleep(0.02)

        def get(path: str):
            r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": f"Bearer {TOKEN}"})
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        return get

    yield start
    for s in servers:
        s.should_exit = True


@pytest.mark.parametrize("helper", [False, True])
def test_the_page_says_whether_it_started_with_the_switch(page, helper):
    get = page(helper)
    status, _, body = get("/api/desktop-helper")
    assert status == 200 and json.loads(body) == {"on": helper}
    _, headers, _ = get("/")
    assert headers["content-security-policy"] == csp(helper)


@pytest.mark.parametrize("helper", [False, True])
def test_this_page_row_names_the_switch_only_when_it_is_on(page, helper):
    _, _, body = page(helper)("/api/connections")
    ui = next(e for e in json.loads(body)["entities"] if e["id"] == "ui")
    if helper:
        assert ui["status"].startswith("Desktop helper switch on") and "127.0.0.1:47800" in ui["status"]
    else:
        assert "status" not in ui  # off: the browser adds a line only when its key is set (connections.js)
