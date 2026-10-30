"""Build the logo files from one source (issue 89) and keep the web page's icons in step with them.

    python scripts/make_logo_assets.py            write docs/assets/*.svg and *.png, update the web page icons
    python scripts/make_logo_assets.py --check    exit 1 if any generated file or the web page is out of date

The mark is a W of servers on a bench: teal rounded squares are running nodes, the dashed amber square is the node the
memory governor holds back. `logo.svg` is the full mark; `logo-small.svg` is the cut for 16 to 32 px (heavier strokes, no
legs). Both follow the viewer's light or dark scheme. The PNGs need Playwright (`pip install -e '.[browser]'`)."""
import base64
import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs/assets"
PAGE = ROOT / "webui/static/index.html"

STYLE = ("<style>.m{fill:#0b8f80}.i{fill:#14211f}.l{fill:none;stroke:#14211f;stroke-linecap:round;stroke-linejoin:round}"
         ".a{fill:none;stroke:#b86e00;stroke-dasharray:7 6;stroke-linecap:round}"
         "@media (prefers-color-scheme:dark){.m{fill:#2fc4b2}.i{fill:#e5eeec}.l{stroke:#e5eeec}.a{stroke:#f0a030}}</style>")
LABEL = "workbench logo: servers linked in a W on a bench"
FULL = """<g>
<polyline class="l" points="16,22 38,78 53,55" stroke-width="8"/><polyline class="l" points="67,55 82,78 104,22" stroke-width="8"/>
<rect class="m" x="5" y="11" width="22" height="22" rx="6"/><rect class="m" x="27" y="67" width="22" height="22" rx="6"/>
<rect class="m" x="71" y="67" width="22" height="22" rx="6"/><rect class="m" x="93" y="11" width="22" height="22" rx="6"/>
<rect class="a" x="49" y="33" width="22" height="22" rx="6" stroke-width="4.5"/>
<rect class="i" x="8" y="94" width="104" height="10" rx="3.5"/><rect class="i" x="18" y="104" width="9" height="12" rx="2"/><rect class="i" x="93" y="104" width="9" height="12" rx="2"/>
</g>"""
SMALL = """<g>
<polyline class="l" points="16,20 38,74 51.4,54" stroke-width="11"/><polyline class="l" points="68.6,54 82,74 104,20" stroke-width="11"/>
<rect class="m" x="2" y="6" width="28" height="28" rx="7"/><rect class="m" x="24" y="60" width="28" height="28" rx="7"/>
<rect class="m" x="68" y="60" width="28" height="28" rx="7"/><rect class="m" x="90" y="6" width="28" height="28" rx="7"/>
<rect class="a" x="46" y="26" width="28" height="28" rx="7" stroke-width="6"/>
<rect class="i" x="6" y="95" width="108" height="14" rx="4"/>
</g>"""


def svg(body: str, label: str = LABEL) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" role="img" aria-label="{label}">\n'
            f"{STYLE}\n{body}\n</svg>\n")


def page_header_svg() -> str:
    """The same small cut inline in the page header, painted from the page's own theme tokens (see .top h1 .logo)."""
    body = SMALL.replace('class="l"', 'class="pl"').replace('class="m"', 'class="pm"').replace('class="a"', 'class="pa"').replace(
        'class="i"', 'class="pi"')
    return f'<svg class="logo" viewBox="0 0 120 120" aria-hidden="true">{body}</svg>'


def data_svg(text: str) -> str:
    return "data:image/svg+xml," + quote(" ".join(text.split()), safe="/=,;")


def data_png(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def outputs() -> dict[Path, str]:
    return {ASSETS / "logo.svg": svg(FULL), ASSETS / "logo-small.svg": svg(SMALL)}


def render_pngs() -> None:
    from playwright.sync_api import sync_playwright

    small, full = svg(SMALL), svg(FULL)
    jobs = [("favicon-16.png", small, 16, None), ("favicon-32.png", small, 32, None),
            ("apple-touch-icon.png", full, 180, "#f7faf9"), ("logo-512.png", full, 512, None)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, text, size, ground in jobs:
            page = browser.new_page(viewport={"width": size, "height": size}, color_scheme="light")
            pad = round(size * 0.14) if ground else 0
            page.set_content(f'<body style="margin:0;background:{ground or "transparent"};display:grid;place-items:center;'
                             f'width:{size}px;height:{size}px">'
                             f'<img src="{data_svg(text)}" width="{size - 2 * pad}" height="{size - 2 * pad}"></body>')
            page.screenshot(path=str(ASSETS / name), omit_background=ground is None)
            page.close()
        browser.close()


ICON_LINKS = (
    ('<link rel="icon" type="image/svg+xml" href="{svg}">', lambda: data_svg(svg(SMALL))),
    ('<link rel="icon" type="image/png" sizes="32x32" href="{png32}">', lambda: data_png(ASSETS / "favicon-32.png")),
    ('<link rel="apple-touch-icon" href="{apple}">', lambda: data_png(ASSETS / "apple-touch-icon.png")),
)
LINK_RES = (r'<link rel="icon" type="image/svg\+xml" href="[^"]*">', r'<link rel="icon" type="image/png" sizes="32x32" href="[^"]*">',
            r'<link rel="apple-touch-icon" href="[^"]*">')


def page_html() -> str:
    """The web page with its three icon links and header mark rebuilt from the generated files."""
    text = PAGE.read_text()
    for (tmpl, make), pattern in zip(ICON_LINKS, LINK_RES, strict=True):
        link = tmpl.split("href=")[0] + f'href="{make()}">'
        text, n = re.subn(pattern, lambda _m, link=link: link, text, count=1)
        assert n == 1, f"icon link not found in the web page: {pattern}"
    text, n = re.subn(r'<h1>(?:<i aria-hidden="true">◈</i>|<svg class="logo".*?</svg>) opti-sim</h1>',
                      lambda _m: f"<h1>{page_header_svg()} opti-sim</h1>", text, count=1, flags=re.DOTALL)
    assert n == 1, "page header not found in the web page"
    return text


def check() -> list[str]:
    bad = [str(p.relative_to(ROOT)) for p, want in outputs().items() if not p.is_file() or p.read_text() != want]
    bad += [f"docs/assets/{n}" for n in ("favicon-16.png", "favicon-32.png", "apple-touch-icon.png", "logo-512.png")
            if not (ASSETS / n).is_file()]
    if not bad and PAGE.read_text() != page_html():
        bad.append("webui/static/index.html (icons or header mark)")
    return bad


def main() -> int:
    if "--check" in sys.argv:
        bad = check()
        for b in bad:
            print(f"out of date: {b}")
        return 1 if bad else 0
    for path, text in outputs().items():
        path.write_text(text)
    render_pngs()
    PAGE.write_text(page_html())
    print("wrote docs/assets/logo.svg, logo-small.svg, favicon-16.png, favicon-32.png, apple-touch-icon.png, logo-512.png; updated the web page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
