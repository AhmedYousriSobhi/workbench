"""The logo files, the web page icons and the README agree (issue 89)."""
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs/assets"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def test_generated_files_and_web_page_are_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/make_logo_assets.py"), "--check"], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + "regenerate with: python scripts/make_logo_assets.py"


def test_svgs_are_valid_accessible_and_follow_the_color_scheme():
    for name in ("logo.svg", "logo-small.svg"):
        text = (ASSETS / name).read_text()
        root = ET.fromstring(text)
        assert root.attrib["viewBox"] == "0 0 120 120" and root.attrib["role"] == "img" and root.attrib["aria-label"]
        assert "prefers-color-scheme:dark" in text, name
        assert re.search(r"stroke-dasharray", text), "the governed node must stay dashed"


def test_png_sizes():
    for name, size in (("favicon-16.png", 16), ("favicon-32.png", 32), ("apple-touch-icon.png", 180), ("logo-512.png", 512)):
        assert png_size(ASSETS / name) == (size, size), name


def test_readme_shows_the_logo_with_alt_text_and_stays_short():
    text = (ROOT / "README.md").read_text()
    assert re.search(r'<img src="docs/assets/logo\.svg" alt="[^"]{8,}"', text)
    assert len(text.splitlines()) <= 100


def test_web_page_title_has_the_logo_not_a_text_glyph():
    page = (ROOT / "webui/static/index.html").read_text()
    assert '<svg class="logo"' in page and "◈" not in page
