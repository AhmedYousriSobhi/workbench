"""The categorical series colours (issue 642): colour-blind safe, never a status colour, fixed order."""
import itertools

from palette_lib import PAL, THEMES, mp, tok

STATUS = ("ok", "warn", "warn_text", "bad", "attention", "error", "accent", "accent_text", "accent_fill", "brand", "node_healthy",
          "L0", "L1", "L2", "L3", "L4", "L5")
CHARTS = [f"chart_{i}" for i in range(1, 7)]


def test_six_chart_colours_in_a_fixed_order():
    assert len(PAL["charts"]) == 6 and len(set(PAL["charts"])) == 6


def test_no_chart_colour_equals_a_status_colour_in_any_theme():
    for theme in THEMES:
        for c in CHARTS:
            for s in STATUS:
                assert tok(theme, c) != tok(theme, s), (theme, c, s)


def test_chart_colours_stay_distinguishable_under_every_cvd_in_light_and_dark():
    for theme in ("light", "dark"):
        for a, b in itertools.combinations(CHARTS, 2):
            for kind in mp.CVD:
                assert mp.cvd_distance(tok(theme, a), tok(theme, b), kind) >= 0.05, (theme, a, b, kind)
            assert mp.delta_e(tok(theme, a), tok(theme, b)) >= 0.05, (theme, a, b)
