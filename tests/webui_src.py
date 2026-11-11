"""The Web UI's page as one string for tests that assert on what the page contains.

The page is markup (`index.html`), its stylesheets (`*.css`) and native ES modules (`js/*.js`, ADR 0007), so a check that
used to search one file searches all of them. Order is stable: markup, stylesheet, then modules by name."""
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webui" / "static"


def page_files() -> list[Path]:
    return [STATIC / "index.html", *sorted(STATIC.glob("*.css")), *sorted((STATIC / "js").glob("*.js"))]


def page_source() -> str:
    return "\n".join(p.read_text() for p in page_files())
