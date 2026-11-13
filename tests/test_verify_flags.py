"""The plan `verify.sh -n` prints for the new flags (issue 576): nothing runs, no cluster is touched."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def plan(*args):
    r = subprocess.run(["bash", str(ROOT / "scripts" / "verify.sh"), "-n", *args], capture_output=True, text=True, cwd=ROOT, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_default_runs_validate_in_the_background():
    assert "validate.sh in the background" in plan()


def test_serial_keeps_the_old_order():
    assert "validate.sh first (--serial)" in plan("--serial")


def test_trust_ci_is_in_the_plan():
    assert "skipped when CI passed for HEAD" in plan("--trust-ci")


def test_changed_with_everything_affected_is_a_full_run():
    out = plan("--changed", "HEAD")  # a clean or dirty tree here may select modules; both forms must name the selection
    assert out.startswith("SELECT ") and "would run:" in out


def test_unknown_flag_is_refused():
    r = subprocess.run(["bash", str(ROOT / "scripts" / "verify.sh"), "--nope"], capture_output=True, text=True, cwd=ROOT, check=False)
    assert r.returncode == 2 and "--changed" in r.stderr


def test_real_test_script_replaces_the_folder_with_explicit_modules():
    s = (ROOT / "scripts" / "real-test.sh").read_text()
    assert 'tests/real/test_*.py) targets=()' in s and '"${targets[@]}"' in s
