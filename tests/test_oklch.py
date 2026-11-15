"""The hand-written sRGB / OKLab / OKLCH maths against published reference values (issue 642)."""
import math

import pytest
from palette_lib import mp

# Reference OKLab values for the sRGB primaries (Ottosson, "A perceptual color space for image processing").
REFERENCE = {"#ffffff": (1.0, 0.0, 0.0), "#000000": (0.0, 0.0, 0.0), "#ff0000": (0.62796, 0.22486, 0.12585),
             "#00ff00": (0.86644, -0.23389, 0.17950), "#0000ff": (0.45201, -0.03246, -0.31153)}


@pytest.mark.parametrize("hexv,lab", REFERENCE.items())
def test_oklab_matches_reference(hexv, lab):
    assert mp.hex_oklab(hexv) == pytest.approx(lab, abs=2e-4)


def test_round_trip_is_exact_for_hex_colours():
    for h in ("#0b8f80", "#2fc4b2", "#14211f", "#e5eeec", "#b86e00", "#f0a030", "#ff5d5d", "#5cb8ff", "#c49bff", "#123456"):
        assert mp.oklab_hex(*mp.hex_oklab(h)) == h


def test_oklch_round_trip_and_gamut_clipping():
    L, C, h = mp.hex_oklch("#2fc4b2")
    assert mp.oklch_hex(L, C, h) == "#2fc4b2"
    clipped = mp.oklch_hex(0.7, 0.5, 2.0)             # far outside sRGB: chroma shrinks, lightness stays
    assert mp.hex_oklch(clipped)[0] == pytest.approx(0.7, abs=0.02)
    assert mp.hex_oklch(clipped)[1] < 0.5
    assert mp.oklch_hex(1.5, 0.1, 1.0) == "#ffffff" and mp.oklch_hex(-1, 0.1, 1.0) == "#000000"


def test_wcag_contrast_reference_values():
    assert mp.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert mp.contrast("#ffffff", "#ffffff") == pytest.approx(1.0)
    assert mp.contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)    # the well-known smallest grey passing AA
    assert mp.contrast("#0b8f80", "#ffffff") == pytest.approx(4.0, abs=0.05)


def test_cvd_simulation_keeps_greys_and_changes_reds():
    for kind in mp.CVD:
        assert mp.delta_e(mp.simulate("#808080", kind), "#808080") < 0.01
        assert math.dist(mp.hex_oklab(mp.simulate("#ff0000", kind)), mp.hex_oklab("#ff0000")) > 0.05 or kind == "tritanopia"
