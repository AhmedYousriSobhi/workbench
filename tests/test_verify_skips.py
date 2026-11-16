"""scripts/lib-skips.sh: a live step that skipped every test must not print a plain PASS (issue 818)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sh(tmp_path, last_line: str, fn: str) -> str:
    log = tmp_path / "log"
    log.write_text(f"noise\n{last_line}\n")
    r = subprocess.run(["bash", "-c", f". {ROOT}/scripts/lib-skips.sh; {fn}"], capture_output=True, text=True, check=True, env={"LOG": str(log), "PATH": "/usr/bin:/bin"})
    return r.stdout.strip()


def test_counts_and_word(tmp_path):
    assert sh(tmp_path, "=== 3 passed, 2 skipped in 4.1s ===", 'pytest_counts "$LOG"') == "3 2"
    assert sh(tmp_path, "=== 3 passed, 2 skipped in 4.1s ===", 'skip_word "$LOG"') == "PASS"
    assert sh(tmp_path, "=== 5 skipped in 1.2s ===", 'skip_word "$LOG"') == "SKIP-ALL"
    assert sh(tmp_path, "=== 5 passed in 1.2s ===", 'skip_word "$LOG"') == "PASS"
    assert sh(tmp_path, "all fine", 'skip_word "$LOG"') == "PASS"  # a step that is not pytest


def test_note_and_warn(tmp_path):
    assert sh(tmp_path, "=== 3 passed, 2 skipped in 4s ===", 'skip_note "$LOG" real') == "NOTE  2 tests skipped in real (see SKIPPED lines above)"
    assert sh(tmp_path, "=== 5 skipped in 1s ===", 'skip_note "$LOG" real').startswith("WARN  real: every test was skipped, nothing was exercised")
    assert sh(tmp_path, "=== 5 passed in 1s ===", 'skip_note "$LOG" real') == ""
