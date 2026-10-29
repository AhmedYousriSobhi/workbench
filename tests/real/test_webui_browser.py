"""The Web UI in a real headless Chromium: no console errors, no horizontal scroll on a phone, keyboard-operable tabs."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ui_shots


def test_pages_render_cleanly_in_chromium(tmp_path):
    res = ui_shots.capture(tmp_path)
    assert len(res["shots"]) == 8 and all(Path(p).stat().st_size > 5000 for p in res["shots"])
    assert res["console_errors"] == []
    assert res["overflow"] == []
    assert res["a11y"] == []
