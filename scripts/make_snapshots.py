"""Code snapshots (issue 797): a real command and its real output as a terminal card, in a light and a dark SVG.

    python scripts/make_snapshots.py --capture [NAME]   run the commands, save their masked output (docs/assets/snapshots/NAME.txt)
    python scripts/make_snapshots.py                    render NAME-light.svg and NAME-dark.svg from the saved output
    python scripts/make_snapshots.py --check            exit 1 if a card is missing, stale or not generated here
    python scripts/make_snapshots.py --markdown NAME DIR   print the <picture> block for a page in DIR (e.g. docs/manual)

The output is never typed by hand: `--capture` runs each command in the repository root and keeps what it printed, with
the home directory, the user and the host name masked (public repo). Rendering reads only the saved text, so the cards
are reproducible and `--check` needs no cluster. The commands are read-only and need no running stack. Colours are the
palette's tokens (docs/assets/palette.yaml), written as presentation attributes, so a card needs no stylesheet and looks
the same on GitHub, in the wiki and in the page's help (its strict CSP allows no inline style). Text is real SVG text;
the alt text names the command and the key output line.
"""
import getpass
import html
import posixpath
import re
import socket
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/assets/snapshots"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_palette as mp

THEMES = ("light", "dark")
COLS = 92                 # wrap wider lines (continuation indented by two spaces)
FONT, LINE, CHAR = 13, 20, 7.9   # px; CHAR is a monospace advance at 13 px with a little slack
PAD, BAR = 18, 34
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'DejaVu Sans Mono','Liberation Mono',monospace"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# role -> palette token; TEXT_ROLES pairs each text colour with the ground it sits on (tests check 4.5:1 in both themes)
ROLES = {"card": "panel", "bar": "raised", "edge": "line_2", "prompt": "accent_text", "cmd": "text", "out": "text_2",
         "note": "muted", "key": "text", "title": "muted"}
TEXT_ROLES = {"prompt": "card", "cmd": "card", "out": "card", "note": "card", "key": "card", "title": "bar"}


@dataclass(frozen=True)
class Snap:
    cmd: str
    title: str
    key: str                                  # the first output line containing this is the key line (bold, in the alt text)
    keep: tuple[tuple[int, int | None], ...] = ((0, None),)   # slices of the output lines to show; gaps are marked
    used_in: tuple[str, ...] = field(default=())             # pages that show it (tests check each one does)


SNAPSHOTS = {
    "check-machine": Snap("./scripts/check-machine.sh", "check-machine on a machine that qualifies", "READY",
                          used_in=("docs/manual/02-install-and-start.md",)),
    "plan": Snap("./scripts/benchctl plan --cpu-nodes 2 --gpu-nodes 4 --with jira", "plan: the cost before the build", "fits:",
                 keep=((0, 4), (-3, None)), used_in=("README.md", "docs/guides/getting-started.md")),
    "scenario-node-down": Snap("./scripts/benchctl scenario run node-down --dry-run", "A scenario's timeline, without running it",
                               "node-down", used_in=("README.md", "docs/manual/06-scenarios.md")),
    "scenario-client-stress": Snap("./scripts/benchctl scenario run client-stress --dry-run", "client-stress: the first steps of five minutes",
                                   "client-stress", keep=((0, 6), (7, 10), (-1, None)), used_in=("docs/guides/client-testing.md",)),
}


# ---- capture ---------------------------------------------------------------------------------------------------------

def mask(text: str) -> str:
    """No home path, user or host name in a public file."""
    text = ANSI.sub("", text).replace(str(ROOT), "~/workbench").replace(str(Path.home()), "/home/user")
    for word, stand_in in ((getpass.getuser(), "user"), (socket.gethostname(), "host")):
        if len(word) > 2:
            text = re.sub(rf"\b{re.escape(word)}\b", stand_in, text)
    return text


def capture(name: str, run=subprocess.run) -> str:
    snap = SNAPSHOTS[name]
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home()), "COLUMNS": "100", "NO_COLOR": "1", "TERM": "dumb", "LANG": "C.UTF-8"}
    r = run(["bash", "-c", snap.cmd], cwd=ROOT, capture_output=True, text=True, timeout=180, check=False, env=env)
    out = mask((r.stdout + r.stderr).rstrip("\n"))
    if not out.strip():
        raise SystemExit(f"{name}: the command printed nothing (exit {r.returncode})")
    return out + "\n"


# ---- render ----------------------------------------------------------------------------------------------------------

def colours(theme: str) -> dict[str, str]:
    tok = mp.resolve_all(mp.load())[theme]
    return {role: tok[t] for role, t in ROLES.items()}


def wrap(line: str) -> list[str]:
    """Wrap at a space (continuation indented by two), keeping the runs of spaces that align columns."""
    if len(line) <= COLS:
        return [line]
    return textwrap.wrap(line, COLS, subsequent_indent="  ", replace_whitespace=False, break_on_hyphens=False) or [line]


def _gap(n: int) -> str:
    return f"... ({n} line{'s' if n != 1 else ''} left out)"


def rows(name: str, output: str) -> list[tuple[str, str]]:
    """(role, text) per shown line: the command, the kept output (wrapped), and a muted note for every gap."""
    snap, lines = SNAPSHOTS[name], output.rstrip("\n").splitlines()
    shown, last = [], 0
    spans = sorted({(s if s >= 0 else max(0, len(lines) + s), len(lines) if e is None else (e if e >= 0 else len(lines) + e))
                    for s, e in snap.keep})
    key_done = False
    for start, end in spans:
        start = max(start, last)
        if start > last:
            shown.append(("note", _gap(start - last)))
        for ln in lines[start:end]:
            role = "out"
            if not key_done and snap.key in ln:
                role, key_done = "key", True
            shown += [(role, w) for w in wrap(ln)]
        last = max(last, end)
    if last < len(lines):
        shown.append(("note", _gap(len(lines) - last)))
    return [("cmd", snap.cmd)] + shown


def key_line(name: str, output: str) -> str:
    snap = SNAPSHOTS[name]
    line = next((ln.strip() for ln in output.splitlines() if snap.key in ln), output.splitlines()[0].strip())
    return line.split(". ")[0]      # the first sentence is enough for alt text


def alt(name: str, output: str) -> str:
    return f"Terminal: {SNAPSHOTS[name].cmd} (prints: {key_line(name, output)})"


def svg(name: str, output: str, theme: str) -> str:
    c, snap, body = colours(theme), SNAPSHOTS[name], rows(name, output)
    width = round(2 * PAD + CHAR * max(COLS // 2, max(len(t) + (2 if r == "cmd" else 0) for r, t in body)))
    height = BAR + PAD + LINE * len(body) + PAD - 6
    e = html.escape
    parts = [f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="{c["card"]}" stroke="{c["edge"]}"/>',
             f'<path d="M0.5 {BAR}V10.5a10 10 0 0 1 10-10H{width - 10.5}a10 10 0 0 1 10 10V{BAR}Z" fill="{c["bar"]}" stroke="{c["edge"]}"/>',
             "".join(f'<circle cx="{PAD + i * 16}" cy="{BAR / 2}" r="5" fill="none" stroke="{c["edge"]}" stroke-width="1.5"/>' for i in range(3)),
             f'<text x="{PAD + 48}" y="{BAR / 2 + 4.5}" fill="{c["title"]}" font-family="{e(MONO)}" font-size="12">{e(snap.title)}</text>']
    y = BAR + PAD + 8
    for role, text in body:
        if role == "cmd":
            parts.append(f'<text x="{PAD}" y="{y}" font-weight="700" xml:space="preserve"><tspan fill="{c["prompt"]}">$ </tspan>'
                         f'<tspan fill="{c["cmd"]}">{e(text)}</tspan></text>')
        else:
            weight = ' font-weight="700"' if role == "key" else ' font-style="italic"' if role == "note" else ""
            parts.append(f'<text x="{PAD}" y="{y}" fill="{c[role]}"{weight} xml:space="preserve">{e(text)}</text>')
        y += LINE
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
            f'aria-labelledby="t d" font-family="{e(MONO)}" font-size="{FONT}">\n'
            f'<title id="t">{e(snap.title)}</title><desc id="d">{e(alt(name, output))}</desc>\n' + "\n".join(parts) + "\n</svg>\n")


def saved(name: str) -> str:
    return (OUT / f"{name}.txt").read_text(encoding="utf-8")


def outputs() -> dict[Path, str]:
    out = {}
    for name in SNAPSHOTS:
        text = saved(name)
        for theme in THEMES:
            out[OUT / f"{name}-{theme}.svg"] = svg(name, text, theme)
    return out


def check() -> list[str]:
    bad = [f"{n}.txt (run --capture {n})" for n in SNAPSHOTS if not (OUT / f"{n}.txt").is_file()]
    if bad:
        return bad
    bad = [_show(p) for p, want in outputs().items() if not p.is_file() or p.read_text(encoding="utf-8") != want]
    known = {f"{n}{s}" for n in SNAPSHOTS for s in (".txt", "-light.svg", "-dark.svg")}
    return bad + [f"{_show(p)} (not generated here)" for p in sorted(OUT.glob("*")) if p.name not in known]


def _show(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else p.name


def markdown(name: str, page_dir: str) -> str:
    """The block a page pastes: GitHub and the help pick the dark card by the theme; the italic line is the caption."""
    base = posixpath.relpath("docs/assets/snapshots", page_dir)
    a = html.escape(alt(name, saved(name)))
    return (f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="{base}/{name}-dark.svg">\n'
            f'  <img src="{base}/{name}-light.svg" alt="{a}">\n</picture>\n\n*{SNAPSHOTS[name].title}*\n')


def main(argv: list[str]) -> int:
    if "--capture" in argv:
        names = [a for a in argv if a in SNAPSHOTS] or list(SNAPSHOTS)
        OUT.mkdir(parents=True, exist_ok=True)
        for n in names:
            (OUT / f"{n}.txt").write_text(capture(n), encoding="utf-8")
            print(f"captured {n}")
    if "--check" in argv:
        bad = check()
        for b in bad:
            print(f"out of date: {b}")
        return 1 if bad else 0
    if "--markdown" in argv:
        i = argv.index("--markdown")
        print(markdown(argv[i + 1], argv[i + 2] if len(argv) > i + 2 else "."), end="")
        return 0
    for p, text in outputs().items():
        p.write_text(text, encoding="utf-8")
    print(f"wrote {2 * len(SNAPSHOTS)} cards to docs/assets/snapshots/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
