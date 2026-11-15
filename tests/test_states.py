"""Interaction states are generated, not picked (issue 642): each steps off its surface and keeps its text readable."""
import pytest
from palette_lib import THEMES, mp, tok


@pytest.mark.parametrize("theme", THEMES)
def test_hover_and_active_step_off_the_panel_by_lightness(theme):
    panel_l = mp.hex_oklab(tok(theme, "panel"))[0]
    hover_l = mp.hex_oklab(tok(theme, "hover"))[0]
    active_l = mp.hex_oklab(tok(theme, "active"))[0]
    assert abs(hover_l - panel_l) >= 0.015, theme
    assert abs(active_l - panel_l) > abs(hover_l - panel_l), theme      # a deeper press than a hover
    assert tok(theme, "row_hover") == tok(theme, "hover") and tok(theme, "row_selected") == tok(theme, "select_bg")


@pytest.mark.parametrize("theme", THEMES)
def test_selected_row_differs_from_hover_and_from_the_panel(theme):
    assert tok(theme, "row_selected") not in (tok(theme, "panel"), tok(theme, "hover"))


@pytest.mark.parametrize("theme", THEMES)
def test_visited_is_not_the_accent_and_disabled_is_not_the_muted_text(theme):
    assert tok(theme, "visited") != tok(theme, "accent_text")
    assert tok(theme, "disabled") != tok(theme, "muted")
    assert mp.contrast(tok(theme, "disabled"), tok(theme, "muted")) < mp.contrast(tok(theme, "panel"), tok(theme, "muted")) + 3


@pytest.mark.parametrize("theme", THEMES)
def test_accent_hover_moves_the_right_way(theme):
    fill_l = mp.hex_oklab(tok(theme, "accent_fill"))[0]
    hover_l = mp.hex_oklab(tok(theme, "accent_hover"))[0]
    assert (hover_l < fill_l) if theme == "light" else (hover_l > fill_l), theme
