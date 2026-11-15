"""The prefers-contrast: more block (issue 642): firmer borders, quieter text promoted, thicker rings."""
import re

import pytest
from palette_lib import ROOT, THEMES, mp, tok

CSS = (ROOT / "webui/static/tokens.css").read_text()
MORE = CSS[CSS.index("@media (prefers-contrast: more)"):CSS.index("@media (forced-colors: active)")]


@pytest.mark.parametrize("theme", THEMES)
def test_line_strong_is_a_3_to_1_border_on_every_surface(theme):
    for s in ("ground", "panel"):
        assert mp.contrast(tok(theme, "line_strong"), tok(theme, s)) >= 3.0, (theme, s)


def test_every_theme_has_a_more_contrast_rule_with_the_firmer_values():
    for theme in THEMES:
        assert f"--line:{tok(theme, 'line_strong')};" in MORE, theme
        assert f"--dim:{tok(theme, 'text_2')};" in MORE, theme
        assert f"--fg-2:{tok(theme, 'text')};" in MORE, theme


def test_the_promoted_text_still_passes():
    for theme in THEMES:
        for s in ("ground", "panel", "raised"):
            assert mp.contrast(tok(theme, "text_2"), tok(theme, s)) >= 4.5
            assert mp.contrast(tok(theme, "text"), tok(theme, s)) >= 7.0, (theme, s)


def test_rings_get_thicker():
    assert len(re.findall(r"--ring-w:3px", MORE)) >= 5
