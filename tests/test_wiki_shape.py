import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_wiki_shape as c


def test_published_pages_have_no_walls_of_text():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check_wiki_shape.py")], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout


def test_measure_flags_long_paragraph_and_long_run():
    assert c.measure("word " * 200)["para"] == 200
    assert c.measure("# H\n" + "line\n\n" * 130)["run"] > c.MAX_RUN
    m = c.measure("# H\n\n> | a | b |\n> |---|---|\n> | 1 | 2 |\n\n- x\n- y\n```\ncode\n```\n")
    assert m["tables"] == 1 and m["bullets"] == 2 and m["code"] == 1 and m["para"] == 0
