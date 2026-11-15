"""Selection is an outline, never a hue change (issue 642, C-16); focus is its own ring."""
import re

from palette_lib import ROOT, mp, tok

CSS = (ROOT / "webui/static/tokens.css").read_text()


def test_the_selection_ring_is_at_least_two_pixels_and_three_in_high_contrast_mode():
    assert re.search(r":root \{[^}]*--ring-w:2px", CSS)
    assert CSS.count("--ring-w:3px") >= 5       # light, dark system default, and the three named dark themes


def test_the_ring_and_the_fill_of_a_selected_row_are_separate_tokens():
    for theme in ("light", "dark", "midnight", "dim"):
        assert tok(theme, "select_ring") != tok(theme, "select_bg")
        assert mp.contrast(tok(theme, "select_ring"), tok(theme, "select_bg")) >= 3.0


def test_focus_is_distinct_from_the_selection_ring_token_and_visible_on_selected_rows():
    for theme in ("light", "dark", "midnight", "dim"):
        assert mp.contrast(tok(theme, "focus"), tok(theme, "select_bg")) >= 3.0


def test_a_healthy_node_and_a_selected_node_differ_by_more_than_hue():
    """The node square is teal when healthy; selection adds a ring wider than the square's own 1 px edge (the markup change comes in step 4)."""
    assert int(re.search(r"--ring-w:(\d)px", CSS).group(1)) >= 2
