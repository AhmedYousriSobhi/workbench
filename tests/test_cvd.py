"""Colour-blind checks (issue 642): the six planes and the status colours under deuteranopia, protanopia, tritanopia."""
import itertools

import pytest
from palette_lib import PAL, THEMES, mp, tok

ALL = (*THEMES, "canvas")
PLANES = [f"plane_{p}" for p in mp.PLANES]
# Pairs that must stay apart by colour alone, not only by dash: the new teal against the colours it sits beside.
HARD_PAIRS = [("ok", "plane_monitoring"), ("ok", "plane_data"), ("ok", "plane_control")]
MIN_HARD = 0.05
MIN_SOFT = 0.06     # below this two plane colours rely on their line types (they always differ, see test_dashes)


def worst(theme, a, b):
    return min(mp.cvd_distance(tok(theme, a), tok(theme, b), k) for k in mp.CVD)


@pytest.mark.parametrize("theme", ALL)
def test_teal_stays_apart_from_its_neighbours(theme):
    for a, b in HARD_PAIRS:
        assert worst(theme, a, b) >= MIN_HARD, (theme, a, b, worst(theme, a, b))


@pytest.mark.parametrize("theme", ALL)
def test_planes_that_merge_under_cvd_differ_by_dash(theme):
    for a, b in itertools.combinations(PLANES, 2):
        if worst(theme, a, b) < MIN_SOFT:
            assert PAL["planes"][a[6:]]["dash"] != PAL["planes"][b[6:]]["dash"], (theme, a, b)


def test_the_plane_colours_are_all_different_without_cvd():
    for theme in ALL:
        for a, b in itertools.combinations(PLANES, 2):
            assert mp.delta_e(tok(theme, a), tok(theme, b)) >= 0.04, (theme, a, b)
