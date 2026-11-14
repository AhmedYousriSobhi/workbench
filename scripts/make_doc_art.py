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
import make_palette as mp
from make_logo_assets import FULL as LOGO_BODY  # the mark, so the hero uses the real logo

PLANES = ("access", "control", "k8s", "data", "monitoring", "management")
_RES = mp.resolve_all(mp.load())      # standalone SVGs cannot read tokens.css, so they get the palette's literal values (issue 647)


def _pair(token: str) -> tuple[str, str]:
    return (_RES["light"][token], _RES["dark"][token])


# token: (light, dark), from docs/assets/palette.yaml
PALETTE = {"surface": _pair("ground"), "surface2": _pair("raised"), "line": _pair("line_2"), "ink": _pair("text"), "muted": _pair("muted"),
           "teal": _pair("brand"), "amber": _pair("attention"), "red": _pair("bad"), **{p: _pair(f"plane_{p}") for p in PLANES}}
# text class -> (text token, ground token); the test checks both themes
TEXT_PAIRS = {"tx": ("ink", "surface"), "tm": ("muted", "surface"), "tx2": ("ink", "surface2"), "tm2": ("muted", "surface2")}
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
    "group-agents.svg": lambda: icon("For AI agents", "A terminal window with a prompt and two small nodes beside it.",
                                     '<rect class="p2" x="6" y="14" width="34" height="36" rx="6"/>' + label(12, 38, "&gt;_", "tx2", "b", True)
                                     + node(43, 14, "ok", 14) + node(43, 34, "ok", 14)),
}

# ---- chapter heroes (issue 596): a small scene per manual chapter, 720 by 150 -----------------------------------------

def _hero(title: str, name: str, desc: str, body: str, animated: bool = False) -> str:
    return svg(720, 150, name, desc, label(24, 34, title, "tx", "h b") + body, animated)


def hero_what_it_is() -> str:
    return _hero("What workbench is", "Chapter 1: what workbench is",
                 "A cloud of stand-in services and a rack of nodes, with the governor badge beside them.",
                 cloud(40, 62, "stand-ins") + wire("M130 90H250") + rack(250, 66, 4, ("ok", "ok", "held", "ok")) + governor(520, 74)
                 + label(40, 132, "fake the APIs, real Slurm in small containers, a governor outside", "tm", "s"), True)


def hero_install_and_start() -> str:
    steps = ("check the machine", "tools", "governor", "cluster")
    b = []
    for i, nm in enumerate(steps):
        x = 40 + i * 170
        b.append(node(x, 56, "ok") + label(x + 13, 106, nm, "tx", "s b", anchor=True))
        if i < 3:
            b.append(wire(f"M{x + 34} 69H{x + 162}", "access"))
    return _hero("Install and start", "Chapter 2: install and start",
                 "Four steps in a row, each with a tick: check the machine, tools, governor, cluster.", "".join(b)
                 + label(40, 136, "./scripts/benchctl start does all four, no sudo", "tm", "mono s"), True)


def hero_using_the_cluster() -> str:
    return _hero("Using the cluster", "Chapter 3: using the cluster",
                 "A login node, the Slurm controller and a rack of nodes on one wire, with a cloud of mocks above.",
                 '<rect class="rk" x="40" y="66" width="64" height="44" rx="10"/>' + label(72, 93, "login", "tx2", "s", anchor=True)
                 + wire("M104 88H150") + '<rect class="rk" x="150" y="66" width="84" height="44" rx="10"/>' + label(192, 93, "slurmctld", "tx2", "s", anchor=True)
                 + wire("M234 88H290") + rack(290, 66, 4) + cloud(540, 56, "mocks"), True)


def hero_nodes_and_shells() -> str:
    return _hero("Nodes and shells", "Chapter 4: nodes and shells",
                 "A terminal on the left, the login node in the middle as the only door, and a rack of nodes behind it.",
                 '<rect class="p2" x="40" y="62" width="70" height="48" rx="8"/>' + label(75, 92, "&gt;_", "tx2", "b", True, True)
                 + wire("M110 86H180") + '<rect class="rk" x="180" y="64" width="84" height="44" rx="10"/>' + label(222, 91, "login", "tx2", "s", anchor=True)
                 + wire("M264 86H330") + rack(330, 64, 4) + label(330, 132, "ssh, only through the login node", "tm", "s"), True)


def hero_web_page() -> str:
    return _hero("The web page", "Chapter 5: the web page",
                 "A browser window with a small circuit of nodes joined by wires, and the governor socket beneath it.",
                 '<rect class="p2" x="40" y="52" width="440" height="72" rx="10"/>' + node(64, 70, "ok", 22) + node(104, 70, "ok", 22) + node(144, 70, "down", 22)
                 + wire("M86 81H104", "monitoring") + wire("M126 81H144", "access") + label(190, 86, "sample data", "tm2", "s")
                 + label(500, 80, "talks only to", "tm", "s") + label(500, 100, "the governor socket", "tx", "s b"), True)


def hero_scenarios() -> str:
    steps = (("0s", "power off"), ("5s", "drain"), ("40s", "recover"))
    b = [wire("M60 88H640", "management")]
    for i, (t, nm) in enumerate(steps):
        x = 60 + i * 230
        b.append(f'<circle class="p2" cx="{x}" cy="88" r="7"/>' + label(x, 70, t, "tx", "s b mono", True, True) + bubble(x - 10, 100, 100, nm))
    return _hero("Scenarios and faults", "Chapter 6: scenarios and faults",
                 "A timeline with three steps: power off at 0 seconds, drain at 5 seconds, recover at 40 seconds.", "".join(b), True)


def hero_governor() -> str:
    b = [governor(40, 62)]
    for i, (lv, st) in enumerate((("L0", "ok"), ("L1", "ok"), ("L3", "held"), ("L4", "down"))):
        x = 190 + i * 120
        b.append(node(x, 62, st) + label(x + 13, 112, lv, "tx", "s b mono", anchor=True))
        if i < 3:
            b.append(wire(f"M{x + 30} 75H{x + 108}", "management", False))
    return _hero("The governor", "Chapter 7: the governor",
                 "The governor badge beside a ladder of four levels: normal, warn, pause and stop, with the marks growing more serious.",
                 "".join(b) + label(40, 136, "pauses first, stops last, lowest priority first", "tm", "s"), True)


def hero_configuration() -> str:
    return _hero("Configuration", "Chapter 8: configuration",
                 "One file, sim.yaml, with arrows to the generated compose file, slurm.conf and targets.",
                 panel(40, 52, 120, 62) + label(100, 88, "sim.yaml", "tx2", "b mono", True, True)
                 + wire("M160 83H300", "management") + bubble(300, 50, 150, "docker-compose.yml", True) + bubble(300, 90, 150, "slurm.conf", True)
                 + bubble(470, 70, 190, "targets, inventories", False)
                 + label(40, 136, "edit one file; the rest is generated", "tm", "s"), True)


def hero_troubleshooting() -> str:
    return _hero("Troubleshooting", "Chapter 9: troubleshooting",
                 "Two commands, status and diag, leading to one masked tar file to attach to a bug report.",
                 bubble(40, 60, 150, "benchctl status", True) + bubble(40, 96, 150, "benchctl diag", True) + wire("M190 90H330", "monitoring")
                 + panel(330, 58, 150, 62) + label(405, 84, "diag.tar.gz", "tx2", "b mono", True, True) + label(405, 104, "secrets masked", "tm2", "s", anchor=True), True)


def hero_reference() -> str:
    return _hero("Reference", "Chapter 10: reference",
                 "The command grammar as three chips: verb, resource, name.", bubble(40, 62, 110, "benchctl", True) + bubble(170, 62, 80, "VERB", True)
                 + bubble(270, 62, 110, "RESOURCE", True) + bubble(400, 62, 80, "NAME", True)
                 + label(40, 126, "one command, one grammar, like kubectl", "tm", "s"))


def hero_which_command() -> str:
    return _hero("Which command?", "Chapter 11: which command do I use?",
                 "A fork in the road: start, look, break, with a command at the end of each branch.",
                 wire("M40 84H200", "access", False) + wire("M200 84L330 56H420", "monitoring", False) + wire("M200 84H420", "access", False)
                 + wire("M200 84L330 112H420", "management", False) + bubble(420, 42, 120, "get nodes", True) + bubble(420, 70, 120, "start", True)
                 + bubble(420, 98, 120, "scenario run", True))


def hero_glossary() -> str:
    return _hero("Glossary", "Chapter 12: glossary",
                 "Three word bubbles: profile, mock, scenario.", bubble(40, 62, 130, "profile", False) + bubble(190, 62, 130, "mock", False)
                 + bubble(340, 62, 130, "scenario", False) + label(40, 126, "words used across the manual and the web page", "tm", "s"))


def map_of_commands() -> str:
    cols = (("Start and stop", "access", ("start", "up", "down", "stop", "open")),
            ("Look", "monitoring", ("get", "describe", "status", "logs", "diag")),
            ("Poke and break", "management", ("exec", "ssh", "submit", "scenario", "set node")))
    b = [label(24, 34, "Map of commands", "tx", "h b")]
    for i, (t, plane, verbs) in enumerate(cols):
        x = 24 + i * 226
        b.append(panel(x, 52, 216, 200) + label(x + 14, 78, t, "tx2", "b"))
        b.append(wire(f"M{x + 14} 90H{x + 202}", plane, False))
        for j, v in enumerate(verbs):
            b.append(bubble(x + 14 + (j % 2) * 96, 104 + (j // 2) * 44, 92, v, True))
    return svg(720, 270, "Map of benchctl commands", "Three panels group the commands: start and stop, look, and poke and break.", "".join(b))


# ---- guides (issue 605) ---------------------------------------------------------------------------------------------

def hero_memory() -> str:
    return _hero("How much memory", "Guide: how much memory the workbench uses",
                 "A memory bar with a filled part for what is expected and a dashed cap beyond it.",
                 '<rect class="p2" x="40" y="62" width="400" height="30" rx="8"/><rect class="ok" x="40" y="62" width="150" height="30" rx="8"/>'
                 + '<rect class="warn" x="40" y="62" width="400" height="30" rx="8"/>' + label(40, 116, "expected use", "tx", "s b") + label(300, 116, "the cap", "tm", "s")
                 + label(40, 138, "measured, plus 15 percent, never above the cap", "tm", "s"), False)


def diagram_memory() -> str:
    b = [label(24, 34, "How the plan counts one part", "tx", "h b"), '<rect class="p2" x="24" y="64" width="672" height="34" rx="8"/>',
         '<rect class="ok" x="24" y="64" width="300" height="34" rx="8"/>', '<rect class="p2" x="324" y="64" width="90" height="34" rx="0" style="stroke:var(--teal)"/>',
         '<rect class="warn" x="24" y="64" width="672" height="34" rx="8"/>',
         label(24, 122, "measured (largest working set seen)", "tx", "s b"),
         label(334, 122, "+ 15 percent", "tx", "s b"), label(600, 122, "cap", "tx", "s b", anchor=True),
         label(24, 160, "expected = min(cap, measured x 1.15)", "tx", "mono s"),
         label(24, 190, "The plan adds the 1 GB reserve for the app under test, then compares with free memory.", "tm", "s")]
    return svg(720, 220, "How the plan counts memory", "A bar for one part: the measured use, 15 percent on top, and a dashed cap that the result never passes.", "".join(b))


def hero_nhc() -> str:
    return _hero("NHC checks", "Guide: NHC checks",
                 "A row of check files, each with a tick, feeding a node that gets drained when one fails.",
                 bubble(40, 58, 110, "gpu-xid", True) + bubble(40, 94, 110, "gpu-hot", True) + wire("M150 88H260", "monitoring")
                 + node(260, 75, "ok") + node(300, 75, "held") + label(40, 140, "every 30 s, one line per problem, drain with NHC: reason", "tm", "s"), True)


def diagram_nhc() -> str:
    b = [label(24, 34, "Three ways in, one set of files", "tx", "h b")]
    for i, t in enumerate(("benchctl add/set/remove check", "edit the files, then reload", "web page: Health checks")):
        b.append(bubble(24, 56 + i * 44, 240, t, True))
        b.append(wire(f"M264 {71 + i * 44}H340", "management", False))
    b.append(panel(340, 52, 160, 130) + label(420, 80, "validate", "tx2", "b", anchor=True) + label(420, 102, "on a copy first", "tm2", "s", anchor=True)
             + label(420, 130, "then write the", "tm2", "s", anchor=True) + label(420, 148, "nhc.d files", "tm2", "mono s", anchor=True))
    b.append(wire("M500 117H560", "monitoring", False))
    b.append(rack(560, 92, 3, ("ok", "ok", "held")) + label(612, 160, "nodes read within 30 s", "tm", "s", anchor=True))
    return svg(720, 210, "How a health check change reaches the nodes", "Three ways of changing a check, through one validation step, to the node files read within 30 seconds.", "".join(b))


def hero_topology() -> str:
    return _hero("Topology", "Guide: Slurm topology and a switch going down",
                 "A spine switch over two leaf switches, each holding a small rack of nodes.",
                 bubble(220, 44, 90, "spine1", True) + wire("M265 74V84H130V96", "access", False) + wire("M265 74V84H400V96", "access", False)
                 + rack(80, 96, 2) + rack(350, 96, 2, ("down", "down")) + label(80, 92, "leaf1", "tx", "s b mono", True) + label(440, 125, "leaf2 down", "tx", "s b mono", True), True)


def diagram_topology() -> str:
    b = [label(24, 34, "A leaf goes down: its nodes drain", "tx", "h b"), bubble(300, 50, 120, "spine1", True),
         wire("M360 80V96H170V116", "access", False), wire("M360 80V96H550V116", "access", False)]
    b.append(bubble(110, 116, 120, "leaf1", True) + rack(100, 160, 3) + label(170, 230, "cn001 cn003 gpu001", "tm", "mono s", anchor=True))
    b.append(bubble(490, 116, 120, "leaf2 down", True) + rack(480, 160, 3, ("down", "down", "down")) + label(550, 230, "drain: switch leaf2 down", "tm", "mono s", anchor=True))
    return svg(720, 250, "A leaf switch failing", "A spine over two leaves; the second leaf is down and its three nodes are drained with a reason.", "".join(b))


def hero_qos() -> str:
    return _hero("QOS and preemption", "Guide: QOS, priority and preemption",
                 "An urgent job arrow pushing a scavenger job back to the queue.",
                 bubble(40, 58, 110, "scavenger", True) + bubble(40, 94, 110, "urgent", True) + arrow(150, 109, 250, 88) + rack(260, 66, 2, ("held", "ok"))
                 + label(40, 140, "the urgent job requeues the scavenger job", "tm", "s"), False)


def diagram_qos() -> str:
    steps = (("0s", "scavenger runs", "ok"), ("20s", "urgent arrives", "held"), ("50s", "scavenger requeued", "down"), ("after", "it runs again", "ok"))
    b = [label(24, 34, "preempt-burst, step by step", "tx", "h b"), wire("M70 110H650", "management", False)]
    for i, (t, name, st) in enumerate(steps):
        x = 110 + i * 166
        b.append(node(x - 13, 64, st) + label(x, 56, t, "tx", "s b mono", True, True) + bubble(x - 70, 136, 140, name, False))
    return svg(720, 190, "Preemption in four steps", "A timeline: the scavenger job runs, an urgent job arrives, the scavenger job is requeued, then runs again.", "".join(b))


def hero_power() -> str:
    return _hero("Power and reboot", "Guide: power save and reboot through the BMC farm",
                 "A node going dark and coming back, with a BMC chip beside it.",
                 node(60, 62, "ok") + wire("M96 75H180", "management") + node(180, 62, "down") + wire("M216 75H300", "management") + node(300, 62, "ok")
                 + label(60, 124, "power down, then power up, through the BMC", "tm", "s"), True)


def diagram_power() -> str:
    b = [label(24, 34, "One command, every view agrees", "tx", "h b"), bubble(24, 70, 210, "scontrol power down cn002", True), wire("M234 85H300", "management", False),
         panel(300, 56, 150, 58) + label(375, 82, "power.sh", "tx2", "b mono", True, True) + label(375, 100, "SuspendProgram", "tm2", "s", anchor=True)]
    for i, t in enumerate(("BMC: ForceOff", "BCM mock: DOWN", "GPU exporter stops")):
        b.append(wire(f"M450 85L500 {58 + i * 34}", "monitoring", False) + bubble(500, 44 + i * 34, 190, t, True))
    b.append(label(24, 170, "sinfo shows POWERED_DOWN, Redfish shows PowerState Off", "tm", "s"))
    return svg(720, 190, "What a power down touches", "A power down command runs one script that tells the BMC, the BCM mock and the GPU exporter.", "".join(b))


def hero_slurm_resilience() -> str:
    return _hero("Slurm resilience", "Guide: Slurm resilience",
                 "A job process on a node outliving a restarted daemon.",
                 bubble(40, 58, 120, "slurmd", True) + bubble(40, 94, 120, "slurmstepd + job", True) + label(190, 80, "restarts", "tm", "s") + arrow(180, 90, 260, 90)
                 + node(270, 76, "ok") + label(40, 140, "break something under a running job and read the trace", "tm", "s"), True)


def diagram_slurm_resilience() -> str:
    b = [label(24, 34, "Reading a trace: what stayed the same", "tx", "h b"), panel(24, 56, 310, 110) + label(40, 84, "before", "tx2", "b"),
         label(40, 108, "slurmd=7   slurmstepd=112", "tx2", "mono s"), label(40, 132, "job: RUNNING", "tx2", "mono s"),
         panel(386, 56, 310, 110) + label(402, 84, "after", "tx2", "b"), label(402, 108, "slurmd=190 slurmstepd=112", "tx2", "mono s"), label(402, 132, "job: RUNNING", "tx2", "mono s"),
         wire("M334 111H386", "management", False), label(360, 100, "restart", "tm", "s", anchor=True),
         label(24, 196, "new slurmd pid, same slurmstepd pid: the job outlived the daemon", "tm", "s")]
    return svg(720, 216, "Reading a Slurm trace", "Before and after a slurmd restart: the daemon's pid changes, the step process pid and the job state stay.", "".join(b))


def hero_choose() -> str:
    return _hero("Choose your workbench", "Guide: choose the workbench to build",
                 "Three steps: plan, fits, build.", bubble(40, 62, 120, "plan", True) + wire("M160 77H230", "access", False) + bubble(230, 62, 120, "fits?", True)
                 + wire("M350 77H420", "access", False) + bubble(420, 62, 120, "up", True) + label(40, 126, "see the cost first, build only if it fits", "tm", "s"), True)


def diagram_choose() -> str:
    b = [label(24, 34, "From a wish to a running cluster", "tx", "h b"), bubble(24, 64, 150, "flags or bench.yaml", True), wire("M174 79H220", "access", False),
         bubble(220, 64, 130, "effective sim", True), wire("M350 79H396", "access", False), bubble(396, 64, 110, "plan", True), wire("M506 79H540", "access", False),
         bubble(540, 64, 156, "governor admits", False),
         label(24, 128, "the same effective sim feeds slurm.conf, the BMC farm, GPU metrics, get, describe and export", "tm", "s"),
         label(24, 150, "plan changes nothing and exits 1 when it does not fit", "tm", "s")]
    return svg(720, 176, "How a workbench selection is built", "A selection becomes an effective sim, then a plan, then governor admission, then a start.", "".join(b))


def _flow(title: str, alt: str, desc: str, steps: tuple[str, ...], notes: tuple[str, ...], plane: str = "access") -> str:
    """A row of rounded steps joined by wires, with note lines below (guides, issue 609)."""
    n = len(steps)
    w = int((672 - (n - 1) * 36) / n)
    b = [label(24, 34, title, "tx", "h b")]
    for i, t in enumerate(steps):
        x = 24 + i * (w + 36)
        b.append(bubble(x, 62, w, t, True))
        if i < n - 1:
            b.append(wire(f"M{x + w} 77H{x + w + 36}", plane, False))
    for j, t in enumerate(notes):
        b.append(label(24, 128 + j * 22, t, "tm", "s"))
    return svg(720, 150 + 22 * len(notes), alt, desc, "".join(b))


def _guide_hero(title: str, name: str, desc: str, chips: tuple[str, ...], line: str, plane: str = "access") -> str:
    b = []
    x = 40
    for i, c in enumerate(chips):
        w = 20 + 8 * len(c)
        b.append(bubble(x, 62, w, c, True))
        if i < len(chips) - 1:
            b.append(wire(f"M{x + w} 77H{x + w + 30}", plane, False))
        x += w + 30
    return _hero(title, name, desc, "".join(b) + label(40, 126, line, "tm", "s"), False)


def hero_jira_real() -> str:
    return _guide_hero("Real Jira", "Guide: real Jira", "Two Jira chips: the mock on 8080 and the real one on 8083.", ("mock :8080", "real :8083"), "the real Jira is optional: 2.4 GB, 3 hours per start")


def diagram_jira_real() -> str:
    return _flow("Mock by default, real on request", "Jira mock and real Jira side by side", "The mock answers on 8080; the real Jira Data Center and its Postgres answer on 8083.",
                 ("jira-mock :8080", "Jira DC :8083", "Postgres"), ("--with jira-real starts the last two; neither is in --addons", "the 3 hour test licence counts from each Jira start"))


def hero_jira_contract() -> str:
    return _guide_hero("Jira contract", "Guide: Jira contract", "A recorded shape compared with the mock.", ("real shapes", "test", "jira-mock"), "keys and types only, never values")


def diagram_jira_contract() -> str:
    return _flow("A test fails when the mock drifts", "Recording real Jira shapes and testing the mock against them", "Shapes recorded from real Jira Cloud are compared with the mock's answers by a test.",
                 ("Jira Cloud", "recorded shapes", "mock answers"), ("scripts/record_jira_shapes.py records keys and types once", "tests/test_jira_contract.py compares them on every run"), "monitoring")


def hero_jira_tickets() -> str:
    return _guide_hero("Jira tickets", "Guide: tickets", "A fault opening a ticket in the mock.", ("fault", "ticket", "client"), "scenarios file and move tickets next to real faults")


def diagram_jira_tickets() -> str:
    return _flow("A fault becomes a ticket", "How a node fault becomes a Jira ticket", "A scenario fault drains a node, the ticket bot files a ticket in the mock, and the client searches for it.",
                 ("node drained", "ticket filed", "client search"), ("recovery resolves the ticket; a team moves others along about every 45 s", "the driver can use the standard REST API instead of /sim/tickets"), "management")


def hero_client_setup() -> str:
    return _guide_hero("Client setup", "Guide: client setup by component", "Components in a row: SSH, Slurm, Grafana, Jira, BMC.", ("SSH", "Slurm", "Grafana", "Jira", "BMC"), "one component at a time, each with what you should see")


def diagram_client_setup() -> str:
    b = [label(24, 34, "Where each part is", "tx", "h b")]
    rows = (("SSH and Slurm", "10.141.255.254:22"), ("Grafana", "10.141.255.21:3000"), ("Jira", "127.0.0.1:8080"), ("BMC", "10.148.0.N:443"), ("BCM", "cmsh over SSH"))
    for i, (a, c) in enumerate(rows):
        y = 56 + i * 40
        b.append(bubble(24, y, 180, a, False) + wire(f"M204 {y + 15}H260", "access", False) + bubble(260, y, 230, c, True))
    b.append(label(520, 80, "fallback: 127.0.0.1", "tm", "s") + label(520, 100, "with the published ports", "tm", "s"))
    return svg(720, 270, "Client endpoints", "Each component with its address: SSH and Slurm, Grafana, Jira, BMC and BCM.", "".join(b))


def hero_client_testing() -> str:
    return _guide_hero("Testing a client", "Guide: testing a monitoring client", "A loop: up, point, break, watch, down.", ("up", "point", "break", "watch", "down"), "the checklist for a fresh session")


def diagram_client_testing() -> str:
    return _flow("The loop", "The client testing loop", "Bring the cluster up, point the client at it, run a scenario, watch what the client shows, then stop.",
                 ("up", "point client", "scenario", "watch", "down"), ("a REFUSED start is the governor protecting the host, not a defect", "never fit workbench to one client; make the service behave like the real one"), "monitoring")


def hero_ssh_testing() -> str:
    return _guide_hero("SSH testing", "Guide: SSH connection testing", "A client, the login node and a compute node.", ("client", "login", "cn001"), "break one thing at a time and see what the client says")


def diagram_ssh_testing() -> str:
    return _flow("One way in, one thing broken at a time", "The SSH path and what can be broken", "A client reaches the login node, then a compute node; keys, host key and session limits can each be broken alone.",
                 ("client", "login node", "compute node"), ("break: key refused, host key changed, session limit", "undo each one before the next"), "access")


def hero_canvas() -> str:
    return _guide_hero("Web UI canvas", "Guide: web UI canvas", "Tokens, a wire and a zoom level.", ("tokens", "wire", "zoom"), "for contributors drawing on the page", "monitoring")


def diagram_canvas() -> str:
    return _flow("What a diagram is made of", "Parts of the page's canvas", "Colour tokens, a wire drawn as halo, core and pulse, and the three zoom levels Fleet, Cluster and Pool.",
                 ("--cv tokens", "wire", "Fleet", "Cluster", "Pool"), ("a wire is a halo, a core and a moving pulse, one colour per plane", "the Focused wire capture runs only through the governor, at L0 and L1, one at a time, for at most 5 minutes"), "monitoring")


# ---- architecture page headers (issue 611) -----------------------------------------------------------------------

def arch_01_problem_and_principles() -> str:
    return _guide_hero('Problem and principles', 'Architecture: Problem and principles', 'Chips: one laptop, nine principles.', ('one laptop', 'nine principles'), 'why 16 GB breaks the old lab', 'access')


def arch_02_architecture() -> str:
    return _guide_hero('Architecture overview', 'Architecture: Architecture overview', 'Chips: login, slurmctld, nodes, mocks.', ('login', 'slurmctld', 'nodes', 'mocks'), 'the full component diagram by priority slice', 'access')


def arch_03_components() -> str:
    return _guide_hero('Component decisions', 'Architecture: Component decisions', 'Chips: Slurm, Kubernetes, BMC, BCM, Jira.', ('Slurm', 'Kubernetes', 'BMC', 'BCM', 'Jira'), 'what each component is and why', 'management')


def arch_04_memory_and_profiles() -> str:
    return _guide_hero('Memory contract', 'Architecture: Memory contract', 'Chips: budget, profile, pre-flight.', ('budget', 'profile', 'pre-flight'), 'every component has a cap and a priority class', 'monitoring')


def arch_05_governor() -> str:
    return _guide_hero('The governor', 'Architecture: The governor', 'Chips: signals, ladder, actions.', ('signals', 'ladder', 'actions'), 'placement, signals and the escalation ladder L0 to L5', 'management')


def arch_06_ux_and_scenarios() -> str:
    return _guide_hero('Control, web page and scenarios', 'Architecture: Control, web page and scenarios', 'Chips: benchctl, web page, scenarios.', ('benchctl', 'web page', 'scenarios'), 'one grammar, one page, named faults', 'access')


def arch_07_macos() -> str:
    return _guide_hero('macOS testing', 'Architecture: macOS testing', 'Chips: macOS, external agent.', ('macOS', 'external agent'), 'never run in a VM on this host', 'control')


def arch_08_repository_and_migration() -> str:
    return _guide_hero('Repository layout', 'Architecture: Repository layout', 'Chips: benchctl/, governor/, images/, docs/.', ('benchctl/', 'governor/', 'images/', 'docs/'), 'where things live', 'control')


def arch_09_roadmap_decisions_risks() -> str:
    return _guide_hero('Roadmap, decisions, risks', 'Architecture: Roadmap, decisions, risks', 'Chips: phases, open decisions, risks.', ('phases', 'open decisions', 'risks'), 'what is done, open and risky', 'management')


def arch_10_references() -> str:
    return _guide_hero('References', 'Architecture: References', 'Chips: sources.', ('sources',), 'what was researched', 'monitoring')


def arch_11_governor_as_built() -> str:
    return _guide_hero('The governor as built', 'Architecture: The governor as built', 'Chips: boot order, control cycle, lock time.', ('boot order', 'control cycle', 'lock time'), 'what the code does today', 'management')


def arch_README() -> str:
    return _guide_hero('How it works', 'Architecture: How it works', 'Chips: manual, guides, architecture, ADRs.', ('manual', 'guides', 'architecture', 'ADRs'), 'every Under the hood section in one index', 'monitoring')


ART = {"readme-hero.svg": readme_hero, "readme-how-it-works.svg": readme_how_it_works, "readme-memory.svg": readme_memory, "tour-hero.svg": tour_hero, **ICONS,
       "01-what-it-is-hero.svg": hero_what_it_is, "02-install-and-start-hero.svg": hero_install_and_start,
       "03-using-the-cluster-hero.svg": hero_using_the_cluster,
       "04-nodes-and-shells-hero.svg": hero_nodes_and_shells, "05-web-page-hero.svg": hero_web_page, "06-scenarios-hero.svg": hero_scenarios,
       "07-governor-hero.svg": hero_governor, "08-configuration-hero.svg": hero_configuration, "09-troubleshooting-hero.svg": hero_troubleshooting,
       "10-reference-hero.svg": hero_reference, "11-which-command-hero.svg": hero_which_command, "12-glossary-hero.svg": hero_glossary, "map-of-commands.svg": map_of_commands,
       "guide-memory-hero.svg": hero_memory, "guide-memory-diagram.svg": diagram_memory, "guide-nhc-checks-hero.svg": hero_nhc, "guide-nhc-checks-diagram.svg": diagram_nhc,
       "guide-topology-hero.svg": hero_topology, "guide-topology-diagram.svg": diagram_topology,
       "guide-qos-and-preemption-hero.svg": hero_qos, "guide-qos-and-preemption-diagram.svg": diagram_qos,
       "guide-power-and-reboot-hero.svg": hero_power, "guide-power-and-reboot-diagram.svg": diagram_power,
       "guide-slurm-resilience-hero.svg": hero_slurm_resilience, "guide-slurm-resilience-diagram.svg": diagram_slurm_resilience,
       "guide-choose-workbench-hero.svg": hero_choose, "guide-choose-workbench-diagram.svg": diagram_choose,
       "guide-jira-real-hero.svg": hero_jira_real, "guide-jira-real-diagram.svg": diagram_jira_real,
       "guide-jira-contract-hero.svg": hero_jira_contract, "guide-jira-contract-diagram.svg": diagram_jira_contract,
       "guide-jira-tickets-hero.svg": hero_jira_tickets, "guide-jira-tickets-diagram.svg": diagram_jira_tickets,
       "guide-client-setup-by-component-hero.svg": hero_client_setup, "guide-client-setup-by-component-diagram.svg": diagram_client_setup,
       "guide-client-testing-hero.svg": hero_client_testing, "guide-client-testing-diagram.svg": diagram_client_testing,
       "guide-ssh-connection-testing-hero.svg": hero_ssh_testing, "guide-ssh-connection-testing-diagram.svg": diagram_ssh_testing,
       "guide-web-ui-canvas-hero.svg": hero_canvas, "guide-web-ui-canvas-diagram.svg": diagram_canvas,
       "arch-01-problem-and-principles-hero.svg": arch_01_problem_and_principles,
       "arch-02-architecture-hero.svg": arch_02_architecture,
       "arch-03-components-hero.svg": arch_03_components,
       "arch-04-memory-and-profiles-hero.svg": arch_04_memory_and_profiles,
       "arch-05-governor-hero.svg": arch_05_governor,
       "arch-06-ux-and-scenarios-hero.svg": arch_06_ux_and_scenarios,
       "arch-07-macos-hero.svg": arch_07_macos,
       "arch-08-repository-and-migration-hero.svg": arch_08_repository_and_migration,
       "arch-09-roadmap-decisions-risks-hero.svg": arch_09_roadmap_decisions_risks,
       "arch-10-references-hero.svg": arch_10_references,
       "arch-11-governor-as-built-hero.svg": arch_11_governor_as_built,
       "arch-README-hero.svg": arch_README}


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
