"""Build the web page's colour tokens from one palette file (issue 642).

    python scripts/make_palette.py            write webui/static/tokens.css
    python scripts/make_palette.py --check    exit 1 if tokens.css is out of date (run by validate.sh)
    python scripts/make_palette.py --report   print every theme's tokens with the contrast of the main pairs

Source: docs/assets/palette.yaml (the format is described at the top of that file). The generator needs only the
standard library and PyYAML (already a project dependency); it is deterministic (same input, same bytes, no clock).
Colour maths is in OKLab/OKLCH so ramp steps look even; contrast is WCAG 2 relative luminance."""
import difflib
import math
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PALETTE = ROOT / "docs/assets/palette.yaml"
TOKENS = ROOT / "webui/static/tokens.css"

STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 850, 900, 950, 975)
STEP_L = dict(zip(STEPS, (0.975, 0.955, 0.915, 0.85, 0.76, 0.66, 0.56, 0.46, 0.37, 0.32, 0.27, 0.21, 0.17)))
THEME_ORDER = ("light", "dark", "midnight", "dim")
TOP_KEYS = {"version", "anchors", "primitives", "semantic", "planes", "dashes", "charts", "common", "themes", "canvas"}
PLANES = ("access", "control", "k8s", "data", "monitoring", "management")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class PaletteError(ValueError):
    """One line per problem; the message is what the user sees."""


# ---- colour maths --------------------------------------------------------------------------------------------

def hex_rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def rgb_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def to_lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def from_lin(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def lin_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklab_lin(L: float, a: float, b: float) -> tuple[float, float, float]:
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def hex_oklab(h: str) -> tuple[float, float, float]:
    return lin_oklab(*(to_lin(c) for c in hex_rgb(h)))


def oklab_hex(L: float, a: float, b: float) -> str:
    return rgb_hex(from_lin(c) for c in oklab_lin(L, a, b))


def hex_oklch(h: str) -> tuple[float, float, float]:
    L, a, b = hex_oklab(h)
    return L, math.hypot(a, b), math.atan2(b, a)


def in_gamut(L: float, a: float, b: float) -> bool:
    return all(-0.0005 <= c <= 1.0005 for c in oklab_lin(L, a, b))


def oklch_hex(L: float, C: float, h: float) -> str:
    """OKLCH to hex; chroma is reduced (hue and lightness kept) until the colour fits sRGB."""
    L = max(0.0, min(1.0, L))
    lo, hi = 0.0, C
    if not in_gamut(L, C * math.cos(h), C * math.sin(h)):
        for _ in range(24):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if in_gamut(L, mid * math.cos(h), mid * math.sin(h)) else (lo, mid)
        C = lo
    return oklab_hex(L, C * math.cos(h), C * math.sin(h))


def luminance(h: str) -> float:
    r, g, b = (to_lin(c) for c in hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def delta_e(a: str, b: str) -> float:
    return math.dist(hex_oklab(a), hex_oklab(b))


# Colour-blindness simulation on linear RGB (Machado, Oliveira and Fernandes 2009, severity 1.0).
CVD = {
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritanopia": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}


def simulate(h: str, kind: str) -> str:
    lin = [to_lin(c) for c in hex_rgb(h)]
    return rgb_hex(from_lin(sum(m * c for m, c in zip(row, lin))) for row in CVD[kind])


def cvd_distance(a: str, b: str, kind: str) -> float:
    return delta_e(simulate(a, kind), simulate(b, kind))


def ramp(seed: str, step: int) -> str:
    """Ramp step at a fixed lightness; chroma follows the seed and thins out away from the seed's own lightness."""
    L0, C0, h = hex_oklch(seed)
    L = STEP_L[step]
    return oklch_hex(L, C0 * max(0.15, 1 - ((L - L0) / 0.55) ** 2), h)


# ---- palette loading and checking ----------------------------------------------------------------------------

def load(path: Path = PALETTE) -> dict:
    return yaml.safe_load(path.read_text())


def check_palette(pal: dict) -> None:
    """Raise PaletteError listing every structural problem, one per line."""
    errs: list[str] = []
    for k in sorted(set(pal) - TOP_KEYS):
        errs.append(f"unknown top-level key: {k}")
    for k in sorted(TOP_KEYS - set(pal)):
        errs.append(f"missing top-level key: {k}")
    if errs:
        raise PaletteError("\n".join(errs))
    for name, v in {**pal["primitives"], **{f"anchors.{n}.{s}": c for n, d in pal["anchors"].items() for s, c in d.items()},
                    **{f"planes.{n}": p["color"] for n, p in pal["planes"].items()}}.items():
        if not HEX.match(str(v)):
            errs.append(f"not a #rrggbb colour: {name} = {v!r}")
    for i, v in enumerate(pal["charts"], 1):
        if not HEX.match(str(v)):
            errs.append(f"not a #rrggbb colour: charts[{i}] = {v!r}")
    if sorted(pal["planes"]) != sorted(PLANES):
        errs.append(f"planes must be exactly {', '.join(PLANES)}")
    if len(pal["charts"]) != 6:
        errs.append("charts must list exactly 6 colours")
    for t in THEME_ORDER:
        if t not in pal["themes"]:
            errs.append(f"missing theme: {t}")
    for t in sorted(set(pal["themes"]) - set(THEME_ORDER)):
        errs.append(f"unknown theme: {t}")
    if len(set(pal["semantic"])) != len(pal["semantic"]):
        errs.append("duplicate name in semantic")
    if errs:
        raise PaletteError("\n".join(errs))
    for t in (*THEME_ORDER, "canvas"):
        try:
            resolve_theme(pal, t)
        except PaletteError as e:
            errs.append(str(e))
    if errs:
        raise PaletteError("\n".join(errs))


class Resolver:
    """Turns one theme's value specs into colours (hex strings, or ('alpha', hex, a))."""

    def __init__(self, pal: dict, name: str):
        self.pal, self.name = pal, name
        spec = pal["canvas"] if name == "canvas" else pal["themes"][name]
        base = pal["themes"][spec["extends"]] if "extends" in spec else {}
        self.scheme = spec.get("scheme") or base.get("scheme", "dark")
        self.neutral = spec.get("neutral") or base.get("neutral", "ink")
        self.specs = {**pal["common"], **base, **spec}
        for i, c in enumerate(pal["charts"], 1):
            self.specs[f"chart_{i}"] = {"tune": {"from": c, "min": 3, "against": ["ground", "panel"]}}
        for p, d in pal["planes"].items():
            self.specs[f"plane_{p}"] = {"tune": {"from": d["color"], "min": 3, "against": ["ground", "panel"]}}
        for k in ("scheme", "neutral", "extends"):
            self.specs.pop(k, None)
        self.memo: dict[str, object] = {}
        self.busy: set[str] = set()

    def err(self, msg: str) -> PaletteError:
        return PaletteError(f"{self.name}: {msg}")

    def token(self, name: str):
        if name in self.memo:
            return self.memo[name]
        if name not in self.specs:
            raise self.err(f"undefined token {name}")
        if name in self.busy:
            raise self.err(f"token {name} refers to itself")
        self.busy.add(name)
        try:
            self.memo[name] = self.value(self.specs[name])
        finally:
            self.busy.discard(name)
        return self.memo[name]

    def solid(self, v) -> str:
        return v[1] if isinstance(v, tuple) else v

    def value(self, spec):
        if isinstance(spec, str):
            if spec.startswith("#"):
                if not HEX.match(spec):
                    raise self.err(f"not a #rrggbb colour: {spec}")
                return spec.lower()
            if spec.startswith("@"):
                return self.token(spec[1:])
            if spec.startswith("anchor."):
                try:
                    return self.pal["anchors"][spec[7:]][self.scheme].lower()
                except KeyError:
                    raise self.err(f"unknown anchor {spec}") from None
            m = re.fullmatch(r"([a-z0-9]+)\.(\d+)", spec)
            if m and int(m[2]) in STEPS:
                ramp_name = self.neutral if m[1] == "neutral" else m[1]
                if ramp_name not in self.pal["primitives"]:
                    raise self.err(f"unknown ramp {m[1]}")
                return ramp(self.pal["primitives"][ramp_name], int(m[2]))
            if spec in self.specs:      # a bare token name inside mix/tune lists
                return self.token(spec)
            raise self.err(f"cannot read value {spec!r}")
        if isinstance(spec, dict) and len(spec) == 1:
            (kind, arg), = spec.items()
            if kind == "mix":
                a, b = (self.solid(self.value(x)) for x in arg[:2])
                t = float(arg[2])
                la, lb = hex_oklab(a), hex_oklab(b)
                return oklab_hex(*(x + (y - x) * t for x, y in zip(la, lb)))
            if kind == "alpha":
                return ("alpha", self.solid(self.value(arg[0])), float(arg[1]))
            if kind == "tune":
                return self.tune(arg)
        raise self.err(f"cannot read value {spec!r}")

    def tune(self, arg: dict) -> str:
        start = self.solid(self.value(arg["from"]))
        need = float(arg["min"])
        bgs = [self.solid(self.token(n)) for n in arg["against"]]
        shift = float(arg.get("shift", 0))
        if not shift and all(contrast(start, b) >= need for b in bgs):
            return start
        L, C, h = hex_oklch(start)
        L += shift
        direction = -1 if sum(luminance(b) for b in bgs) / len(bgs) > 0.179 else 1
        for _ in range(300):
            cand = oklch_hex(L, C, h)
            if all(contrast(cand, b) >= need for b in bgs):
                return cand
            L += direction * 0.005
            if not 0 <= L <= 1:
                break
        raise self.err(f"cannot reach {need}:1 for {start} against {', '.join(arg['on'])}")


def resolve_theme(pal: dict, name: str) -> dict:
    """Every semantic token of a theme (or of 'canvas') as hex, or ('alpha', hex, a)."""
    r = Resolver(pal, name)
    want = list(pal["semantic"])
    missing = [n for n in want if n not in r.specs]
    extra = sorted(n for n in r.specs if n not in want)
    if missing or extra:
        raise PaletteError(f"{name}: missing {', '.join(missing) or 'none'}; unknown {', '.join(extra) or 'none'}")
    return {n: r.token(n) for n in want}


def resolve_all(pal: dict) -> dict:
    return {t: resolve_theme(pal, t) for t in (*THEME_ORDER, "canvas")}


# ---- tokens.css ----------------------------------------------------------------------------------------------

CSS_NAMES = {"ground": "bg", "text": "fg", "text_2": "fg-2", "muted": "dim", "unknown": "none", "shadow": "shadow-color"}
CANVAS_ALIASES = {"cv-ground": "ground", "cv-dot": "dot", "cv-surface": "panel", "cv-surface-2": "raised", "cv-border": "line",
                  "cv-border-2": "line_2", "cv-text": "text", "cv-text-2": "text_2", "cv-text-muted": "muted", "cv-error": "error",
                  "cv-attention": "attention", "cv-no-current": "no_current", "cv-wire-base": "wire_base"}


def css_name(token: str) -> str:
    return CSS_NAMES.get(token, token if re.fullmatch(r"L\d", token) else token.replace("_", "-"))


def css_value(v) -> str:
    if isinstance(v, tuple):
        r, g, b = (round(c * 255) for c in hex_rgb(v[1]))
        return f"rgb({r} {g} {b} / {v[2]:g})"
    return v


def decls(tokens: dict, only: set | None = None) -> str:
    return " ".join(f"--{css_name(k)}:{css_value(v)};" for k, v in tokens.items() if only is None or k in only)


def dash_decls(pal: dict) -> str:
    d = {f"plane-{p}-dash": (pal["planes"][p]["dash"] or "none") for p in PLANES}
    d.update({f"{k.replace('_', '-')}-dash": v for k, v in pal["dashes"].items()})
    return " ".join(f"--{k}:{v};" for k, v in d.items())


def contrast_more(tokens: dict) -> str:
    """Raised borders and quieter text one step firmer, rings thicker."""
    return f"--line:{css_value(tokens['line_strong'])}; --dim:{css_value(tokens['text_2'])}; --fg-2:{css_value(tokens['text'])}; --ring-w:3px;"


def render_tokens(pal: dict) -> str:
    res = resolve_all(pal)
    out = ["/* Generated by scripts/make_palette.py from docs/assets/palette.yaml. Do not edit: change the palette and regenerate. */"]
    out.append(f":root {{ color-scheme:light dark; --ring-w:2px; {dash_decls(pal)} }}")
    out.append(f":root, :root[data-theme=light] {{ color-scheme:light; {decls(res['light'])} }}")
    out.append(f"@media (prefers-color-scheme: dark) {{ :root:not([data-theme]) {{ color-scheme:dark; {decls(res['dark'])} }} }}")
    for t in ("dark", "midnight", "dim"):
        out.append(f":root[data-theme={t}] {{ color-scheme:dark; {decls(res[t])} }}")
    # Planes and states are named in markup; the stylesheet colours them (the CSP blocks inline styles).
    for p in PLANES:
        cap = " stroke-linecap:round;" if pal["planes"][p].get("cap") == "round" else ""
        out.append(f"[data-plane={p}] {{ --plane-c:var(--plane-{p}); --plane-dash:var(--plane-{p}-dash);{cap} }}")
    out.append("svg [data-plane] { stroke:var(--plane-c); stroke-dasharray:var(--plane-dash); }")
    for state, tok in (("ok", "ok"), ("attention", "attention"), ("error", "error"), ("down", "node-down"), ("healthy", "node-healthy")):
        out.append(f"[data-state={state}] {{ --state-c:var(--{tok}); }}")
    canvas = res["canvas"]
    aliases = " ".join(f"--{a}:{css_value(canvas[t])};" for a, t in CANVAS_ALIASES.items())
    out.append(f".canvas {{ color-scheme:dark; {decls(canvas)} {aliases} }}")
    out.append("@media (prefers-contrast: more) {")
    out.append(f"  :root, :root[data-theme=light] {{ {contrast_more(res['light'])} }}")
    out.append(f"  :root:not([data-theme]) {{ {contrast_more(res['light'])} }}")
    out.append("  @media (prefers-color-scheme: dark) { :root:not([data-theme]) { " + contrast_more(res["dark"]) + " } }")
    for t in ("dark", "midnight", "dim"):
        out.append(f"  :root[data-theme={t}] {{ {contrast_more(res[t])} }}")
    out.append("}")
    out.append("@media (forced-colors: active) {")
    out.append("  svg [data-plane] { stroke:CanvasText; }")
    out.append("  [data-state] { --state-c:CanvasText; }")
    out.append("}")
    light = res["light"]
    light_aliases = " ".join(f"--{a}:{css_value(light[t])};" for a, t in CANVAS_ALIASES.items())
    out.append(f"@media print {{ :root, .canvas {{ color-scheme:light; {decls(light)} {light_aliases} }} }}")
    return "\n".join(out) + "\n"


def report(pal: dict) -> str:
    res = resolve_all(pal)
    lines = []
    for t, toks in res.items():
        lines.append(f"[{t}]")
        for name, a, b in PAIRS:
            if a in toks and b in toks:
                lines.append(f"  {name:<28} {contrast(css_solid(toks[a]), css_solid(toks[b])):5.2f}")
    return "\n".join(lines)


def css_solid(v) -> str:
    return v[1] if isinstance(v, tuple) else v


# The pairs the contrast test and the generated guide read: (label, foreground, background), minimums in MIN.
PAIRS = [("text on ground", "text", "ground"), ("text on panel", "text", "panel"), ("text_2 on panel", "text_2", "panel"),
         ("muted on panel", "muted", "panel"), ("accent_text on panel", "accent_text", "panel"),
         ("accent_text on ground", "accent_text", "ground"), ("warn_text on panel", "warn_text", "panel"),
         ("bad on panel", "bad", "panel"), ("on_accent on accent_fill", "on_accent", "accent_fill"),
         ("on_accent on accent_hover", "on_accent", "accent_hover"), ("focus on panel", "focus", "panel"),
         ("focus on ground", "focus", "ground"), ("attention on panel", "attention", "panel")]


def main(argv: list[str]) -> int:
    pal = load()
    try:
        check_palette(pal)
    except PaletteError as e:
        print(e)
        return 1
    if "--report" in argv:
        print(report(pal))
        return 0
    text = render_tokens(pal)
    if "--check" in argv:
        cur = TOKENS.read_text() if TOKENS.exists() else ""
        if cur != text:
            print("webui/static/tokens.css is out of date (python scripts/make_palette.py)")
            diff = [ln.rstrip()[:160] for ln in difflib.unified_diff(cur.splitlines(), text.splitlines(), "current", "generated", n=0)]
            print("\n".join(diff[:12]))
            return 1
        return 0
    TOKENS.write_text(text)
    print(f"wrote {TOKENS.relative_to(ROOT)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
