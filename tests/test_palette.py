"""The palette file and its generator (issue 642): anchors, completeness, ramps, schema, determinism, freshness."""
import copy
import itertools
import re
import subprocess
import sys

import pytest
from palette_lib import PAL, RES, ROOT, THEMES, mp


def test_tokens_css_is_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/make_palette.py"), "--check"], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + "regenerate with: python scripts/make_palette.py"


def test_anchors_equal_the_logo_values():
    assert PAL["anchors"] == {"teal": {"light": "#0b8f80", "dark": "#2fc4b2"}, "ink": {"light": "#14211f", "dark": "#e5eeec"},
                              "amber": {"light": "#b86e00", "dark": "#f0a030"}}
    logo = (ROOT / "docs/assets/logo.svg").read_text()       # the logo script reads the palette; the generated file carries the values
    for a in ("teal", "ink", "amber"):
        for scheme in ("light", "dark"):
            assert PAL["anchors"][a][scheme] in logo, (a, scheme)


def test_every_theme_defines_every_semantic_token():
    for t in THEMES:
        assert set(RES[t]) == set(PAL["semantic"]) | set(PAL["component"]), t


def test_ramps_are_monotonic_in_lightness_and_keep_their_hue():
    for name, seed in PAL["primitives"].items():
        steps = [mp.hex_oklch(mp.ramp(seed, s)) for s in mp.STEPS]
        lights = [x[0] for x in steps]
        assert lights == sorted(lights, reverse=True), name
        assert all(b < a for a, b in itertools.pairwise(lights)), name
        h0 = mp.hex_oklch(seed)[2]
        for L, C, h in steps:
            if C > 0.03:      # hue is meaningless on near-greys
                assert abs((h - h0 + 3.1416) % 6.2832 - 3.1416) < 0.35, (name, h, h0)


def test_a_light_teal_graphic_is_not_text():
    """The logo teal is below 4.5:1 on white, so text and buttons use the tuned tokens."""
    assert mp.contrast(PAL["anchors"]["teal"]["light"], "#ffffff") < 4.5
    light = {k: RES["light"][k] for k in ("accent_text", "accent_fill", "warn_text")}
    assert all(mp.contrast(v, "#ffffff") >= 5.5 for v in light.values())


def test_schema_rejects_bad_palettes_with_one_line_each():
    def broken(mutate) -> str:
        p = copy.deepcopy(PAL)
        mutate(p)
        with pytest.raises(mp.PaletteError) as e:
            mp.check_palette(p)
        return str(e.value)

    assert "unknown top-level key: colour" in broken(lambda p: p.update(colour=1))
    assert "missing top-level key: charts" in broken(lambda p: p.pop("charts"))
    assert "not a #rrggbb colour: teal" in broken(lambda p: p["primitives"].update(teal="teal"))
    assert "missing theme: dim" in broken(lambda p: p["themes"].pop("dim"))
    assert "unknown theme: neon" in broken(lambda p: p["themes"].update(neon={}))
    assert "dark: missing text" in broken(lambda p: p["themes"]["dark"].pop("text"))
    assert "undefined token zzz" in broken(lambda p: p["themes"]["dark"].update(text="@zzz"))
    assert "refers to itself" in broken(lambda p: p["themes"]["dark"].update(text="@text"))


def test_tokens_css_has_every_theme_and_the_planes_and_stays_small():
    css = (ROOT / "webui/static/tokens.css").read_text()
    for sel in (":root[data-theme=light]", ":root[data-theme=dark]", ":root[data-theme=midnight]", ":root[data-theme=dim]",
                ":root:not([data-theme])", "@media (prefers-contrast: more)", "@media (forced-colors: active)", "@media print"):
        assert sel in css, sel
    for p in mp.PLANES:
        assert f"--plane-{p}-dash:" in css and f"[data-plane={p}]" in css
    assert ".pl-stroke" in css and ".pl-dash" in css
    assert "--cv-ground:" in css and "legacy" not in PAL
    assert len(css.encode()) < 20_000
    assert not re.search(r"\b(var\(--[a-z0-9-]+\))\s*\)\s*\)", css)
