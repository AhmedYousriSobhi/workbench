"""The page's stylesheets and the help page carry no colour literals (issue 644): colours come from tokens.css."""
import re

from palette_lib import ROOT

COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\bhwb\(|\boklch\(|\blab\(")
ALLOW: dict[str, int] = {}       # file -> number of literals tolerated; empty on purpose: add a token instead


def css_without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def test_stylesheets_have_no_colour_literals():
    for name in ("app.css", "canvas.css"):
        found = COLOUR.findall(css_without_comments((ROOT / "webui/static" / name).read_text()))
        assert len(found) <= ALLOW.get(name, 0), (name, found[:5])


def test_the_page_scripts_have_no_colour_literals():
    """Planes and states are named in markup (data-plane, data-state); tokens.css paints them. Comments may name issues (#517)."""
    for f in sorted((ROOT / "webui/static/js").glob("*.js")):
        code = re.sub(r"//[^\n]*|/\*.*?\*/", "", f.read_text(), flags=re.DOTALL)
        assert not COLOUR.findall(code), (f.name, COLOUR.findall(code)[:5])


def test_the_login_page_and_the_help_style_have_no_colour_literals():
    assert not COLOUR.findall((ROOT / "webui/static/login.html").read_text())
    from webui.wiki import STYLE
    assert not COLOUR.findall(STYLE)


def test_the_page_and_the_help_load_tokens_before_their_own_styles():
    html = (ROOT / "webui/static/index.html").read_text()
    assert html.index("/static/tokens.css") < html.index("/static/app.css") < html.index("/static/canvas.css")
    assert '<link rel="stylesheet" href="/static/tokens.css"><style>{STYLE}</style>' in (ROOT / "webui/wiki.py").read_text()


def test_tokens_css_is_served_under_the_same_policy():
    """The CSP allows 'self' styles only; a linked stylesheet is fine, an inline style is not."""
    app = (ROOT / "webui/app.py").read_text()
    assert "style-src 'self'" in app
