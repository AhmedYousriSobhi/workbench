"""The saved colour theme is applied before first paint (issue 647): a 'self' script, no CSP change, same key and default as shell.js."""
import re

from palette_lib import ROOT

STATIC = ROOT / "webui/static"
INIT = (STATIC / "js/theme-init.js").read_text()
SHELL = (STATIC / "js/shell.js").read_text()
HTML = (STATIC / "index.html").read_text()


def test_the_script_loads_first_and_plainly():
    tag = re.search(r'<script[^>]*theme-init\.js[^>]*>', HTML).group(0)
    assert tag == '<script src="/static/js/theme-init.js">'          # no defer, async or module
    assert HTML.index("theme-init.js") < HTML.index('rel="stylesheet"')
    assert "import " not in INIT and "export " not in INIT                     # a classic script cannot import


def test_the_csp_is_unchanged_and_needs_no_hash():
    app = (ROOT / "webui/app.py").read_text()
    assert "script-src 'self';" in app and "sha256" not in app and "unsafe-inline" not in app


def test_key_default_and_theme_names_match_shell_js():
    assert "localStorage.getItem('workbench-theme') || 'midnight'" in INIT
    assert "localStorage.getItem('workbench-theme') || 'midnight'" in SHELL
    themes = re.search(r"THEMES = \[([^\]]*)\]", SHELL).group(1)
    assert set(re.findall(r"'(\w+)'", themes)) - {"auto"} == set(re.findall(r"'(\w+)'", re.search(r"\[(\'light\'[^\]]*)\]", INIT).group(1)))


def test_it_never_throws_when_storage_is_blocked():
    assert re.search(r"try \{[^}]*localStorage[^}]*\} catch", INIT)
