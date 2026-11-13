"""Build the documentation illustrations from one source (issue 583).

    python scripts/make_doc_art.py            write docs/assets/art/*.svg
    python scripts/make_doc_art.py --check    exit 1 if any file is missing, stale or not generated here
    python scripts/make_doc_art.py --list     print every file with its size

Same idea as make_logo_assets.py: shapes are defined in code, nothing is drawn by hand, no external images. Every SVG
carries its own light and dark styles (`prefers-color-scheme`), real text in a system font stack, a `<title>`, and, when
it animates, a `prefers-reduced-motion` rule that stops the motion (the still frame makes sense alone). Colours come
from PALETTE; every text colour is paired with the ground it sits on in TEXT_PAIRS so a test can check the 4.5:1
contrast in both themes. Status is never colour alone: a node carries a mark (check, pause, cross) as well.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/assets/art"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_logo_assets import FULL as LOGO_BODY  # the mark, so the hero uses the real logo

# token: (light, dark). Planes follow docs/guides/web-ui-canvas.md (dark values are the page's own).
PALETTE = {
    "surface": ("#f4f8f7", "#111b19"), "surface2": ("#e3ece9", "#1b2a27"), "line": ("#9db3ae", "#3a4e4a"),
    "ink": ("#14211f", "#e5eeec"), "muted": ("#44544f", "#a9bbb7"),
    "teal": ("#0b8f80", "#2fc4b2"), "amber": ("#b86e00", "#f0a030"), "red": ("#c0392b", "#ff7b72"),
    "access": ("#1d7fd1", "#5CB8FF"), "control": ("#475569", "#E2E8F0"), "k8s": ("#4f6ad6", "#7C9CFF"),
    "data": ("#8a4fd6", "#C49BFF"), "monitoring": ("#138a5a", "#4BE3A0"), "management": ("#b86e00", "#FFB347"),
}
# text class -> (text token, ground token); the test checks both themes
TEXT_PAIRS = {"tx": ("ink", "surface"), "tm": ("muted", "surface"), "tx2": ("ink", "surface2"), "tm2": ("muted", "surface2")}
PLANES = ("access", "control", "k8s", "data", "monitoring", "management")
SANS = "system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
MAX_BYTES = 60 * 1024


def _style(animated: bool) -> str:
    def block(i: int) -> str:
        v = ";".join(f"--{k}:{c[i]}" for k, c in PALETTE.items())
        return v
    css = [f":root{{{block(0)}}}",
           f"@media (prefers-color-scheme:dark){{:root{{{block(1)}}}}}",
           f"text{{font-family:{SANS};font-size:14px}}.mono{{font-family:{MONO}}}.b{{font-weight:700}}.h{{font-size:22px}}.s{{font-size:12px}}.c{{text-anchor:middle}}",
           ".bg{fill:var(--surface)}.p2{fill:var(--surface2);stroke:var(--line)}.ln{stroke:var(--line);fill:none;stroke-width:2}",
           ".tx{fill:var(--ink)}.tm{fill:var(--muted)}.tx2{fill:var(--ink)}.tm2{fill:var(--muted)}",
           ".ok{fill:var(--teal)}.warn{fill:none;stroke:var(--amber);stroke-width:3;stroke-dasharray:6 5}.bad{fill:var(--red)}",
           ".mk{fill:none;stroke:var(--surface);stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round}",
           ".rk{fill:var(--surface2);stroke:var(--ink);stroke-width:2}.gov{fill:#f0a030}",
           (".m{fill:var(--teal)}.i{fill:var(--ink)}.l{fill:none;stroke:var(--ink);stroke-linecap:round;stroke-linejoin:round}"
            ".a{fill:none;stroke:var(--amber);stroke-dasharray:7 6;stroke-linecap:round}")]
    css += [f".w-{p}{{stroke:var(--{p})}}" for p in PLANES]
    css.append(".halo{fill:none;stroke-width:7;stroke-opacity:.18;stroke-linecap:round}.core{fill:none;stroke-width:2.5;stroke-linecap:round}"
               ".pulse{fill:none;stroke-width:4;stroke-linecap:round;stroke-dasharray:1 23}")
    if animated:
        css.append("@keyframes flow{to{stroke-dashoffset:-48}}.pulse{animation:flow 2.4s linear infinite}"
                   "@media (prefers-reduced-motion:reduce){.pulse{animation:none}}")
    return "<style>" + "".join(css) + "</style>"


def svg(w: int, h: int, title: str, desc: str, body: str, animated: bool = False) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">\n'
            f'<title id="t">{title}</title><desc id="d">{desc}</desc>\n{_style(animated)}\n'
            f'<rect class="bg" width="{w}" height="{h}" rx="16"/>\n{body}\n</svg>\n')


# ---- primitives -----------------------------------------------------------------------------------------------------

def label(x: float, y: float, text: str, cls: str = "tx", size: str = "", mono: bool = False, anchor: bool = False) -> str:
    c = " ".join(p for p in (cls, size, "mono" if mono else "", "c" if anchor else "") if p)
    return f'<text class="{c}" x="{x}" y="{y}">{text}</text>'


def node(x: float, y: float, state: str = "ok", s: int = 26) -> str:
    """Rounded square with a state mark: ok = tick, held = dashed amber outline and pause bars, down = cross."""
    cx, cy = x + s / 2, y + s / 2
    if state == "held":
        return (f'<rect class="warn" x="{x}" y="{y}" width="{s}" height="{s}" rx="7"/>'
                f'<path class="ln" style="stroke:var(--amber);stroke-width:3" d="M{cx-3.5} {cy-6}v12M{cx+3.5} {cy-6}v12"/>')
    if state == "down":
        return (f'<rect class="bad" x="{x}" y="{y}" width="{s}" height="{s}" rx="7"/>'
                f'<path class="mk" d="M{cx-5} {cy-5}l10 10M{cx+5} {cy-5}l-10 10"/>')
    return (f'<rect class="ok" x="{x}" y="{y}" width="{s}" height="{s}" rx="7"/>'
            f'<path class="mk" d="M{cx-6} {cy}l4 4.5l8-9"/>')


def rack(x: float, y: float, n: int, states: tuple[str, ...] = (), s: int = 26) -> str:
    g, pad = 8, 10
    w = n * s + (n - 1) * g + 2 * pad
    out = [f'<rect class="rk" x="{x}" y="{y}" width="{w}" height="{s + 2 * pad}" rx="10"/>']
    for i in range(n):
        out.append(node(x + pad + i * (s + g), y + pad, states[i] if i < len(states) else "ok", s))
    return "".join(out)


def wire(d: str, plane: str = "access", pulse: bool = True) -> str:
    """Base halo, core and a moving pulse: the same recipe as the page's wire.js."""
    out = f'<path class="halo w-{plane}" d="{d}"/><path class="core w-{plane}" d="{d}"/>'
    if pulse:
        out += f'<path class="pulse w-{plane}" d="{d}"/>'
    return out


def arrow(x1: float, y1: float, x2: float, y2: float) -> str:
    return (f'<path class="ln" d="M{x1} {y1}L{x2} {y2}"/>'
            f'<path class="ln" d="M{x2-9} {y2-7}L{x2} {y2}L{x2-9} {y2+7}"/>')


def cloud(x: float, y: float, text: str = "mocks") -> str:
    """A cloud of stand-ins: dashed outline, because they pretend."""
    return (f'<path class="ln" style="stroke-dasharray:5 4" d="M{x} {y+34}a16 16 0 0 1 4-30a22 22 0 0 1 42-4a18 18 0 0 1 26 12a14 14 0 0 1-4 22z"/>'
            + label(x + 38, y + 25, text, "tx", "s", anchor=True))


def governor(x: float, y: float, text: str = "governor") -> str:
    """The bouncer at the door: a badge with a raised-hand bar and a word, never colour alone."""
    return (f'<rect class="gov" x="{x}" y="{y}" width="92" height="34" rx="17"/>'
            f'<text x="{x+46}" y="{y+22}" class="c b s" style="fill:#14211f">{text}</text>')


def bubble(x: float, y: float, w: float, text: str, mono: bool = False) -> str:
    return (f'<rect class="p2" x="{x}" y="{y}" width="{w}" height="30" rx="15"/>'
            + label(x + w / 2, y + 20, text, "tx2", "s", mono, True))


def panel(x: float, y: float, w: float, h: float) -> str:
    return f'<rect class="p2" x="{x}" y="{y}" width="{w}" height="{h}" rx="14"/>'


# ---- illustrations --------------------------------------------------------------------------------------------------

def readme_hero() -> str:
    b = [f'<g transform="translate(28 36) scale(1.9)">{LOGO_BODY}</g>',
         label(280, 76, "workbench", "tx", "h b"),
         label(280, 104, "A pretend HPC cluster for a 16 GB laptop,", "tx"),
         label(280, 126, "with a bouncer that keeps it from eating all your RAM.", "tm"),
         # live cluster strip
         panel(24, 270, 672, 130),
         label(44, 296, "login", "tx2", "s b"), label(172, 296, "slurmctld", "tx2", "s b"), label(334, 296, "nodes", "tx2", "s b"),
         '<rect class="rk" x="40" y="318" width="64" height="44" rx="10"/>' + label(72, 345, "login", "tx2", "s", anchor=True),
         '<rect class="rk" x="168" y="318" width="84" height="44" rx="10"/>' + label(210, 345, "slurmctld", "tx2", "s", anchor=True),
         wire("M104 340H168"), wire("M252 340H330"),
         rack(330, 327, 4, ("ok", "ok", "held", "ok")),
         cloud(500, 304), governor(588, 308),
         label(44, 388, "real Slurm in small containers; the APIs around it are stand-ins", "tm2", "s")]
    return svg(720, 420, "workbench: a pretend HPC cluster with a bouncer",
               "The workbench mark and tagline above a small live cluster: login node to slurmctld to four nodes, one held "
               "back by the governor, a cloud of mocks and the governor badge at the door; the wires pulse.", "".join(b), True)


def readme_how_it_works() -> str:
    pw, gap, y = 160, 10, 24
    names = [("1  Fake the APIs", "Jira, BMC, BCM, GPUs", "mocks"), ("2  Real Slurm", "small containers", "slurm"),
             ("3  The governor", "outside everything", "gov"), ("4  Faults are stunts", "named, repeatable", "stunt")]
    b = []
    for i, (t, sub, kind) in enumerate(names):
        x = 24 + i * (pw + gap)
        b.append(panel(x, y, pw, 196))
        b.append(label(x + 12, y + 28, t, "tx2", "b"))
        b.append(label(x + 12, y + 48, sub, "tm2", "s"))
        cx = x + pw / 2
        if kind == "mocks":
            b.append(cloud(x + 36, y + 80))
            b.append(arrow(cx, y + 124, cx, y + 150))
            b.append(bubble(x + 20, y + 152, 120, "your app", False))
        elif kind == "slurm":
            b.append(rack(x + 14, y + 86, 3, ("ok", "ok", "ok"), 24))
            b.append(label(cx, y + 150, "slurmctld + slurmd", "tx2", "s", anchor=True))
            b.append(label(cx, y + 170, "built from source", "tm2", "s", anchor=True))
        elif kind == "gov":
            b.append(governor(x + 34, y + 76))
            b.append(rack(x + 14, y + 120, 3, ("ok", "held", "ok"), 24))
            b.append(label(cx, y + 178, "pauses before it stops", "tm2", "s", anchor=True))
        else:
            b.append(bubble(x + 20, y + 76, 120, "gpu-xid-storm", True))
            b.append(arrow(cx, y + 110, cx, y + 130))
            b.append(rack(x + 24, y + 128, 3, ("ok", "down", "ok"), 22))
            b.append(label(cx, y + 178, "a node down, then healed", "tm2", "s", anchor=True))
    b.append(wire("M24 240H696", "access", True))
    return svg(720, 258, "How workbench works, in four panels",
               "Four panels: mocks fake the APIs your app calls, real Slurm runs in small containers, the governor sits "
               "outside everything it may pause, and faults are named scenarios.", "".join(b), True)


def readme_memory() -> str:
    """Schematic, not to scale: the numbers live in docs/guides/memory.md and benchctl/memory.py."""
    x0, w, y = 24, 672, 74
    segs = [("OS + desktop", 0.26, "surface2"), ("your apps", 0.26, "surface2"), ("workbench", 0.30, "teal"), ("safety floor", 0.18, "amber")]
    b = [label(24, 36, "A 16 GB laptop, with room left", "tx", "h b"),
         label(24, 56, "Schematic, not to scale. Real numbers: docs/guides/memory.md", "tm", "s")]
    x = x0
    for name, frac, kind in segs:
        sw = w * frac
        if kind == "teal":
            b.append(f'<rect class="ok" x="{x}" y="{y}" width="{sw:.1f}" height="44" rx="6"/>')
        elif kind == "amber":
            b.append(f'<rect class="warn" x="{x+2}" y="{y+2}" width="{sw-4:.1f}" height="40" rx="6"/>')
        else:
            b.append(f'<rect class="p2" x="{x}" y="{y}" width="{sw:.1f}" height="44" rx="6"/>')
        b.append(label(x + sw / 2, y + 66, name, "tx", "s b", anchor=True))
        x += sw
    # profiles inside the workbench slice
    px = x0 + w * 0.52
    b.append(label(px, y + 100, "workbench profiles", "tm", "s"))
    for i, nm in enumerate(("min", "std", "full")):
        b.append(bubble(px + i * 74, y + 110, 66, nm, True))
    # governor ladder
    ly = y + 170
    b.append(label(24, ly, "The governor reacts as free memory shrinks", "tx", "b"))
    ladder = ("L0 normal", "L1 warn", "L2 throttle", "L3 freeze", "L4 stop")
    for i, nm in enumerate(ladder):
        lx = 24 + i * 135
        b.append(bubble(lx, ly + 14, 124, nm, True))
    b.append(arrow(24, ly + 62, 690, ly + 62))
    b.append(label(24, ly + 84, "Thresholds and dwell times: docs/architecture/05-governor.md", "tm", "s"))
    return svg(720, 350, "A 16 GB laptop's memory and the governor ladder",
               "A memory bar split into the OS and desktop, your apps, the workbench with its min, std and full profiles, "
               "and the safety floor, above the governor's levels L0 to L4.", "".join(b))


def tour_hero() -> str:
    """The Tour's map: eight stops on one pulsing wire, a node at the end that goes down and comes back."""
    stops = ("Idea", "Start", "Page", "Zoom", "Shell", "Governor", "Break", "Heal")
    xs = [50 + i * 88 for i in range(len(stops))]
    b = [label(24, 34, "Eight stops, five minutes", "tx", "h b"), wire(f"M{xs[0]} 92L{xs[-1]} 92", "access")]
    for i, (x, nm) in enumerate(zip(xs, stops, strict=True)):
        b.append(f'<circle class="p2" cx="{x}" cy="92" r="15" style="stroke-width:2"/>' + label(x, 97, str(i + 1), "tx2", "b", anchor=True))
        b.append(label(x, 130, nm, "tx", "s b", anchor=True))
    return svg(720, 150, "The Tour: eight stops", "A line of eight numbered stops: idea, start, page, zoom, shell, governor, break, heal.",
               "".join(b), animated=True)


# ---- small icons: the Wiki Home tiles and the sidebar groups (issue 591) ----------------------------------------------

def icon(title: str, desc: str, body: str, animated: bool = False) -> str:
    return svg(64, 64, title, desc, body, animated)


def _bars(*ys: int) -> str:
    return "".join(f'<path class="ln" d="M14 {y}h36"/>' for y in ys)


ICONS = {
    "tile-install.svg": lambda: icon("Install", "A node box with an arrow dropping into it.",
                                     '<path class="ln" d="M32 6v18M24 17l8 8l8-8"/>' + node(19, 32, "ok", 26)),
    "tile-use.svg": lambda: icon("Use", "A terminal window with a prompt.",
                                 '<rect class="p2" x="8" y="12" width="48" height="40" rx="8"/>' + label(16, 38, "&gt;_", "tx2", "b", True)),
    "tile-break.svg": lambda: icon("Break things", "A node with a cross: it is down.", node(19, 19, "down", 26)),
    "tile-watch.svg": lambda: icon("Watch it", "A wire with a moving pulse.",
                                   wire("M8 40C20 16 44 16 56 40", "monitoring"), True),
    "tile-configure.svg": lambda: icon("Configure", "Three sliders.",
                                       _bars(18, 32, 46) + '<circle class="ok" cx="24" cy="18" r="5"/><circle class="ok" cx="40" cy="32" r="5"/>'
                                       '<circle class="ok" cx="30" cy="46" r="5"/>'),
    "tile-fix.svg": lambda: icon("Fix it", "A healthy node inside a circular arrow: it healed.",
                                  '<path class="ln" d="M10 32a22 22 0 0 1 40-13M54 32a22 22 0 0 1-40 13M44 12l7 8l-10 2"/>' + node(19, 19, "ok", 26)),
    "group-manual.svg": lambda: icon("Manual", "An open book.",
                                     '<rect class="p2" x="8" y="14" width="23" height="36" rx="4"/><rect class="p2" x="33" y="14" width="23" height="36" rx="4"/>'),
    "group-guides.svg": lambda: icon("Guides", "A list of steps.", _bars(18, 32, 46)),
    "group-architecture.svg": lambda: icon("Architecture", "A rack of nodes.", rack(5, 18, 2, (), 14)),
    "group-adr.svg": lambda: icon("Decisions", "A note with a tick.",
                                  '<rect class="p2" x="14" y="8" width="36" height="48" rx="6"/><path class="ln" d="M24 32l6 6l11-12"/>'),
}

ART = {"readme-hero.svg": readme_hero, "readme-how-it-works.svg": readme_how_it_works, "readme-memory.svg": readme_memory, "tour-hero.svg": tour_hero, **ICONS}


def outputs() -> dict[Path, str]:
    return {OUT / name: fn() for name, fn in ART.items()}


def check() -> list[str]:
    bad = [str(p.relative_to(ROOT)) for p, want in outputs().items() if not p.is_file() or p.read_text() != want]
    if OUT.is_dir():
        bad += [f"{p.relative_to(ROOT)} (not generated here)" for p in sorted(OUT.glob("*.svg")) if p.name not in ART]
    return bad


def main() -> int:
    if "--check" in sys.argv:
        bad = check()
        for b in bad:
            print(f"out of date: {b}")
        return 1 if bad else 0
    if "--list" in sys.argv:
        for p, text in outputs().items():
            print(f"{len(text.encode()):>7}  {p.relative_to(ROOT)}")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for p, text in outputs().items():
        p.write_text(text)
    print(f"wrote {len(ART)} files to docs/assets/art/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
