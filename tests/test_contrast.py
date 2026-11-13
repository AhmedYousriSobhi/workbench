"""WCAG contrast of every theme's tokens (issue 642): text 4.5:1, graphics and rings 3:1, against every surface."""
import pytest
from palette_lib import RES, SURFACES, THEMES, mp, tok

ALL = (*THEMES, "canvas")
TEXT_ON_SURFACES = ("text", "text_2", "muted", "accent_text", "warn_text", "bad", "ok", "visited")
GRAPHIC_ON_SURFACES = ("focus", "line_strong", "select_ring", "attention", "node_healthy", "L0", "L1", "L2", "L3", "L4", "L5",
                       "plane_access", "plane_control", "plane_k8s", "plane_data", "plane_monitoring", "plane_management",
                       "chart_1", "chart_2", "chart_3", "chart_4", "chart_5", "chart_6", "no_current")


@pytest.mark.parametrize("theme", ALL)
@pytest.mark.parametrize("name", TEXT_ON_SURFACES)
def test_text_tokens_reach_4_5_on_every_surface(theme, name):
    for s in SURFACES:
        assert mp.contrast(tok(theme, name), tok(theme, s)) >= 4.5, (theme, name, s, mp.contrast(tok(theme, name), tok(theme, s)))


@pytest.mark.parametrize("theme", ALL)
@pytest.mark.parametrize("name", GRAPHIC_ON_SURFACES)
def test_graphic_tokens_reach_3_on_ground_and_panel(theme, name):
    for s in ("ground", "panel"):
        assert mp.contrast(tok(theme, name), tok(theme, s)) >= 3.0, (theme, name, s, mp.contrast(tok(theme, name), tok(theme, s)))


@pytest.mark.parametrize("theme", ALL)
def test_text_on_accent_and_text_on_states(theme):
    for fill in ("accent_fill", "accent_hover"):
        assert mp.contrast(tok(theme, "on_accent"), tok(theme, fill)) >= 4.5, (theme, fill)
    for state in ("hover", "active", "row_hover", "row_selected", "select_bg"):
        assert mp.contrast(tok(theme, "text"), tok(theme, state)) >= 4.5, (theme, state)
        assert mp.contrast(tok(theme, "accent_text"), tok(theme, state)) >= 4.5, (theme, state)
    assert mp.contrast(tok(theme, "disabled"), tok(theme, "panel")) >= 3.0


def test_the_logo_anchors_are_graphics_not_text_in_the_light_theme():
    """Brand teal and amber are below 4.5:1 on light grounds; the tuned tokens carry text and buttons."""
    assert mp.contrast(tok("light", "brand"), tok("light", "panel")) < 4.5
    assert mp.contrast(tok("light", "accent_text"), tok("light", "panel")) >= 4.5
    assert mp.contrast(tok("light", "warn_text"), tok("light", "panel")) >= 4.5
    assert tok("light", "brand") == "#0b8f80" and tok("light", "attention") == "#b86e00"
    assert tok("dark", "brand") == "#2fc4b2" and tok("dark", "attention") == "#f0a030"


def test_every_pair_the_report_lists_passes():
    for theme in ALL:
        for label, a, b in mp.PAIRS:
            floor = 3.0 if a in ("focus", "attention") else 4.5
            assert mp.contrast(tok(theme, a), tok(theme, b)) >= floor, (theme, label)


def test_alpha_tokens_are_wash_only():
    """Transparent tokens (sel, shadow) are never contrast-tested, so they must never carry text."""
    alphas = {n for t in ALL for n, v in RES[t].items() if isinstance(v, tuple)}
    assert alphas == {"sel", "shadow"}
