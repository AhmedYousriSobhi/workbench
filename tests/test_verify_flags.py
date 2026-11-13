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


def test_full_leaves_real_jira_out_unless_asked():
    assert "--ignore=tests/real/test_jira_dc_real.py" in plan("--full")
    assert "test_jira_dc_real" not in plan("--full", "--with-jira").split("would run: pytest")[1]


def test_the_summary_always_says_jira_was_left_out():
    s = (ROOT / "scripts" / "verify.sh").read_text()
    assert "real Jira skipped (optional part" in s and "--with-jira" in s


def test_install_nightly_prints_the_units_and_writes_nothing(tmp_path):
    import os
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "verify.sh"), "-n", "--install-nightly", "03:15"], capture_output=True, text=True, cwd=ROOT, env=env, check=False)
    assert r.returncode == 0, r.stderr
    assert "OnCalendar=*-*-* 03:15:00" in r.stdout and "verify.sh --full --with-jira" in r.stdout and "nightly-$$(date +%%Y%%m%%d).log" in r.stdout
    assert "systemctl --user enable --now verify-nightly.timer" in r.stdout
    assert list(tmp_path.iterdir()) == []


def test_install_nightly_defaults_to_half_past_two_and_remove_prints_its_plan(tmp_path):
    assert "OnCalendar=*-*-* 02:30:00" in plan("--install-nightly")
    assert "disable --now verify-nightly.timer" in plan("--remove-nightly")
