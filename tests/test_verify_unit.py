import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/verify.sh", *args], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)


def test_list_units_names_each_component_once():
    r = verify("--list-units")
    names = r.stdout.split()
    assert r.returncode == 0 and "teleport" in names and "nhc" in names and "bmc_tls" in names
    assert names == sorted(set(names)) and not any(n.endswith(("_real", "_browser")) for n in names)


def test_dry_run_shows_the_steps_and_runs_nothing():
    r = verify("--unit", "teleport", "--clean", "--dry-run")
    assert r.returncode == 0 and r.stdout.splitlines() == [
        "would run: ./scripts/benchctl down --yes", "would run: scripts/verify.d/teleport.sh",
        "would run: pytest tests/test_teleport.py", "would run: WORKBENCH_REAL=1 pytest tests/real/test_teleport_real.py"]


def test_without_clean_there_is_no_down_and_no_hook():
    out = verify("--unit", "teleport", "-n").stdout
    assert "benchctl down" not in out and "verify.d" not in out and "tests/real/test_teleport_real.py" in out


def test_a_unit_without_live_tests_runs_only_the_fast_ones():
    out = verify("--unit", "addressing", "-n").stdout
    assert out.strip() == "would run: pytest tests/test_addressing.py"


def test_unknown_or_unsafe_unit_exits_2_and_lists_the_known_ones():
    for bad in ("nosuch", "a;b", "../x", ""):
        r = verify("--unit", bad)
        assert r.returncode == 2 and "teleport" in r.stderr, bad


def test_every_hook_belongs_to_a_unit_with_tests():
    units = set(verify("--list-units").stdout.split())
    hooks = {p.stem for p in (ROOT / "scripts" / "verify.d").glob("*.sh")}
    assert hooks and hooks <= {u for u in units} | {u.split("_")[0] for u in units}
