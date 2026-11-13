"""The documentation art (issue 583): generated, within budget, themed, readable in both themes."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_doc_art", ROOT / "scripts/make_doc_art.py")
art = importlib.util.module_from_spec(spec)
sys.modules["make_doc_art"] = art
spec.loader.exec_module(art)

FILES = sorted(art.OUT.glob("*.svg"))


def _lum(hexcol: str) -> float:
    c = [int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_check_passes():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/make_doc_art.py"), "--check"], check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_every_art_file_exists():
    assert {p.name for p in FILES} == set(art.ART)


def test_budget_title_dark_and_motion():
    for p in FILES:
        text = p.read_text()
        assert len(text.encode()) <= art.MAX_BYTES, p.name
        assert re.search(r"<title[^>]*>[^<]+</title>", text), p.name
        assert "prefers-color-scheme:dark" in text, p.name
        if "animation:" in text:
            assert "prefers-reduced-motion:reduce" in text, p.name


def test_text_uses_known_classes():
    for p in FILES:
        for cls in re.findall(r'<text class="([^"]*)"', p.read_text()):
            assert set(cls.split()) & set(art.TEXT_PAIRS), f"{p.name}: text without a paired colour class: {cls}"


def test_text_contrast_both_themes():
    for name, (fg, bg) in art.TEXT_PAIRS.items():
        for i, theme in enumerate(("light", "dark")):
            assert ratio(art.PALETTE[fg][i], art.PALETTE[bg][i]) >= 4.5, f"{name} {theme}"
    # the governor badge text is a fixed dark ink on a fixed amber, in both themes
    assert ratio("#14211f", "#f0a030") >= 4.5
