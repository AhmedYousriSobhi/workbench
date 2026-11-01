"""Run shell commands and render their real output as a terminal screenshot (needs: pip install -e '.[browser]').

    .venv/bin/python scripts/term_shot.py OUT.png "cmd one" "cmd two" ... [--title T] [--lines N] [--timeout S] [--prelude SH]

Each command runs in the repository root through bash; stdout and stderr are shown under a `$ ` prompt line. Colour
codes are stripped, the home directory and user name are masked (public docs, see CLAUDE.md), and output longer than
--lines keeps its first and last lines. Used for the screenshots in docs/guides and docs/manual: the output is never typed
by hand. --prelude is shell run before every command and not shown (helper functions the page defines in its text).
"""
import argparse
import getpass
import html
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PAGE = """<!doctype html><meta charset=utf-8><body style="margin:0;background:#fff;padding:16px">
<div id=t style="width:{width}px;background:#1e2227;color:#d7dae0;border-radius:8px;overflow:hidden;
font:14px/1.45 'DejaVu Sans Mono',Menlo,Consolas,monospace;box-shadow:0 2px 8px #0003">
<div style="background:#333a42;padding:8px 12px;font-size:12px;color:#aab2bd">{title}</div>
<pre style="margin:0;padding:12px 14px;white-space:pre-wrap;word-break:break-all">{body}</pre></div>"""


def mask(text: str) -> str:
    text = ANSI.sub("", text).replace(str(ROOT), "~/workbench")
    text = text.replace(str(Path.home()), "/home/user")
    return re.sub(rf"\b{re.escape(getpass.getuser())}\b", "user", text)


def run(cmd: str, timeout: float, lines: int, prelude: str = "") -> str:
    shown = "\n".join(("$ " if i == 0 else "> ") + ln for i, ln in enumerate(cmd.splitlines()))
    try:
        r = subprocess.run(["bash", "-c", prelude + "\n" + cmd], cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
        out = (r.stdout + r.stderr).rstrip("\n")
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode() + (e.stderr or b"").decode()).rstrip("\n") + "\n[timed out]"
    rows = mask(out).splitlines()
    if len(rows) > lines:
        rows = rows[: lines // 2] + [f"... ({len(rows) - lines} lines left out)"] + rows[-(lines // 2):]
    return html.escape(mask(shown)) + ("\n" + html.escape("\n".join(rows)) if rows else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("commands", nargs="+")
    ap.add_argument("--title", default="terminal")
    ap.add_argument("--lines", type=int, default=24)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--prelude", default="")
    ap.add_argument("--width", type=int, default=980)
    a = ap.parse_args()
    body = "\n\n".join(run(c, a.timeout, a.lines, a.prelude) for c in a.commands)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": a.width + 40, "height": 400}, device_scale_factor=1.5)
        page.set_content(PAGE.format(width=a.width, title=html.escape(a.title), body=body))
        page.locator("#t").screenshot(path=str(out))
        b.close()
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
