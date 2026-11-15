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


def test_a_long_how_to_paragraph_or_item_is_flagged():
    long = "word " * (c.MAX_STEP + 1)
    assert c.long_steps(f"# H\n\n{long}\n") == [(3, c.MAX_STEP + 1)]
    assert c.long_steps(f"- {long}\n  more\n") == [(1, c.MAX_STEP + 2)]
    assert c.long_steps("1. " + "word " * c.MAX_STEP) == []  # at the limit is fine
    # a quote, a table, a code block and a Source line are not procedures
    assert c.long_steps(f"> {long}\n\n| {long} |\n\n```\n{long}\n```\n\nSource: {long}\n") == []


def test_step_todo_list_only_shrinks():
    for rel in c.STEP_TODO:
        page = ROOT / rel
        assert page.is_file(), f"{rel} is gone: remove it from STEP_TODO"
        assert c.long_steps(page.read_text(encoding="utf-8")), f"{rel} now passes: remove it from STEP_TODO"
        assert page.parent.name in c.STEP_GROUPS, rel
