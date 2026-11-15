"""Shared loading for the colour-system tests (issue 642): the generator, the palette and every resolved theme."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_palette as mp

PAL = mp.load()
RES = mp.resolve_all(PAL)
THEMES = list(mp.THEME_ORDER)
SURFACES = ("ground", "panel", "raised")


def solid(v) -> str:
    return v[1] if isinstance(v, tuple) else v


def tok(theme: str, name: str) -> str:
    return solid(RES[theme][name])
