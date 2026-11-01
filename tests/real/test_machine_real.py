"""The initial machine check on the real host, run the way a new user runs it (no venv)."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = str(ROOT / "scripts" / "check-machine.sh")


def test_check_machine_script_runs_with_system_python_and_reports_ready():
    r = subprocess.run([SCRIPT], check=False, cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert "docker daemon: running" in r.stdout and "profiles:" in r.stdout, r.stdout
    assert r.returncode == 0 and "READY" in r.stdout and "NOT READY" not in r.stdout, r.stdout


def test_check_machine_fails_loudly_when_docker_is_missing():
    with tempfile.TemporaryDirectory() as d:  # a PATH that has python3 and bash but no docker
        os.symlink("/usr/bin/python3", f"{d}/python3")
        os.symlink(shutil.which("bash"), f"{d}/bash")
        r = subprocess.run(["bash", SCRIPT], check=False, cwd=ROOT, env={"PATH": d},
                           capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "docker: not installed" in r.stdout and "NOT READY" in r.stdout, r.stdout
