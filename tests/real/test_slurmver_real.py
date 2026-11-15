"""A selectable Slurm version (issue 221, ADR 0079): the daemons and the client tools run the release that was asked for.
Not run by the agent: `./scripts/verify.sh --unit slurmver --clean`. The first run compiles that Slurm (several minutes); later runs reuse the image."""
import os
import subprocess
from pathlib import Path

import pytest
from helpers import wait_for

from benchctl import slurmver, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
OTHER = "25.11.9"


def benchctl(*args: str, timeout: int = 3000) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts/benchctl"), *args], capture_output=True, text=True, timeout=timeout, check=False)


def run(container: str, *cmd: str) -> str:
    r = subprocess.run(["docker", "exec", f"workbench-{container}-1", *cmd], capture_output=True, text=True, timeout=60, check=False)
    return (r.stdout + r.stderr).strip()


@pytest.fixture(scope="module")
def built():
    os.chdir(ROOT)
    out = benchctl("up", "--cpu-nodes", "1", "--gpu-nodes", "0", "--slurm-version", OTHER)
    assert out.returncode == 0, out.stdout + out.stderr
    assert wait_for(lambda: "idle" in run("login", "sinfo", "-h", "-o", "%t"), timeout=120), run("login", "sinfo")
    yield
    stack.down(say=lambda m: None)
    workbench.forget()


def test_every_daemon_and_tool_reports_the_requested_release(built):
    assert run("slurmctld", "slurmctld", "-V") == f"slurm {OTHER}"
    assert run("cn001", "slurmd", "-V") == f"slurm {OTHER}"
    assert run("login", "sinfo", "--version") == f"slurm {OTHER}"


def test_the_images_carry_the_versions_own_tag_and_the_pin_is_not_replaced(built):
    for name in ("node-base", "slurm-ctl", "login"):
        tag = subprocess.run(["docker", "image", "inspect", f"workbench/{name}:dev-{OTHER}", "--format", "{{.Id}}"], capture_output=True, text=True, check=False)
        assert tag.returncode == 0 and tag.stdout.strip(), name
    assert OTHER != slurmver.DEFAULT


def test_a_job_runs_on_that_release(built):
    r = subprocess.run(["docker", "exec", "-u", "alice", "workbench-login-1", "srun", "-p", "cpu", "-N1", "hostname"], capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0 and "cn001" in r.stdout, r.stdout + r.stderr


def test_an_unlisted_version_is_refused_before_anything_starts():
    r = benchctl("plan", "--slurm-version", "24.11.1", timeout=120)
    assert r.returncode != 0 and "24.11.1" in (r.stdout + r.stderr) and "25.11.9" in (r.stdout + r.stderr)
