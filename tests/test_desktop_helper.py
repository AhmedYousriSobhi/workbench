"""The page-title token script and its CSP switch (issue 983): off by default, one loopback origin when enabled."""
import importlib
import re

import pytest
from palette_lib import ROOT

STATIC = ROOT / "webui/static"
JS = (STATIC / "js/desktop-helper.js").read_text()
HTML = (STATIC / "index.html").read_text()


@pytest.fixture
def csp(monkeypatch):
    """Rebuilds webui.app's CSP for an env value; reloads it once more on teardown, even after a failed assert."""
    from webui import app

    def build(value):
        if value is None:
            monkeypatch.delenv("WORKBENCH_DESKTOP_HELPER", raising=False)
        else:
            monkeypatch.setenv("WORKBENCH_DESKTOP_HELPER", value)
        return importlib.reload(app).CSP

    yield build
    monkeypatch.undo()  # back to the real environment before the final reload
    importlib.reload(app)


def test_csp_is_unchanged_by_default(csp):
    default = csp(None)
    assert "connect-src 'self';" in default and "127.0.0.1" not in default
    assert csp("0") == default


def test_csp_adds_exactly_one_loopback_origin_when_enabled(csp):
    on = csp("1")
    assert "connect-src 'self' http://127.0.0.1:47800;" in on
    assert on.count("127.0.0.1") == 1 and "unsafe-inline" not in on


def test_script_is_opt_in_and_never_throws():
    assert "localStorage.getItem('workbench-desktop-helper') === '1'" in JS
    assert JS.index("if (!on) return;") < JS.index("fetch(")
    assert ".catch(" in JS and re.search(r"try \{[^}]*getItem", JS)
    assert "document.title = base + ' ' + t" in JS and "{16,}" in JS  # only a plausible token is appended


def test_script_is_a_plain_deferred_self_script():
    assert '<script src="/static/js/desktop-helper.js" defer></script>' in HTML
    assert "import " not in JS and "export " not in JS
