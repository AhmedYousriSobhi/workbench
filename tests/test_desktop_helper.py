"""The page-title token script and its CSP switch (issue 983): off by default, one loopback origin when enabled."""
import importlib
import re

from palette_lib import ROOT

STATIC = ROOT / "webui/static"
JS = (STATIC / "js/desktop-helper.js").read_text()
HTML = (STATIC / "index.html").read_text()


def _csp(monkeypatch, value):
    from webui import app

    if value is None:
        monkeypatch.delenv("WORKBENCH_DESKTOP_HELPER", raising=False)
    else:
        monkeypatch.setenv("WORKBENCH_DESKTOP_HELPER", value)
    return importlib.reload(app).CSP


def test_csp_is_unchanged_by_default(monkeypatch):
    csp = _csp(monkeypatch, None)
    assert "connect-src 'self';" in csp and "127.0.0.1" not in csp
    assert _csp(monkeypatch, "0") == csp
    _csp(monkeypatch, None)


def test_csp_adds_exactly_one_loopback_origin_when_enabled(monkeypatch):
    csp = _csp(monkeypatch, "1")
    assert "connect-src 'self' http://127.0.0.1:47800;" in csp
    assert csp.count("127.0.0.1") == 1 and "unsafe-inline" not in csp
    _csp(monkeypatch, None)


def test_script_is_opt_in_and_never_throws():
    assert "localStorage.getItem('workbench-desktop-helper') === '1'" in JS
    assert JS.index("if (!on) return;") < JS.index("fetch(")
    assert ".catch(" in JS and re.search(r"try \{[^}]*getItem", JS)
    assert "document.title = base + ' ' + t" in JS and "{16,}" in JS  # only a plausible token is appended


def test_script_is_a_plain_deferred_self_script():
    assert '<script src="/static/js/desktop-helper.js" defer></script>' in HTML
    assert "import " not in JS and "export " not in JS
