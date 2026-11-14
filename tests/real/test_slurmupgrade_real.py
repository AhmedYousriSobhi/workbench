"""Slurm upgraded in place under a running job (issue 221, ADR 0079): versions per daemon, and the job and its slurmstepd survive.
Not run by the agent: `./scripts/verify.sh --unit slurmupgrade --clean`. The first run compiles the older release and builds the
newer image when missing (several minutes each); later runs reuse them."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import scenario, slurmver, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
FROM, TO = "25.11.9", slurmver.DEFAULT


def benchctl(*args: str, timeout: int = 3000) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts/benchctl"), *args], capture_output=True, text=True, timeout=timeout, check=False)


def run(container: str, *cmd: str) -> str:
    r = subprocess.run(["docker", "exec", f"workbench-{container}-1", *cmd], capture_output=True, text=True, timeout=60, check=False)
    return (r.stdout + r.stderr).strip()


def started(container: str, daemon: str) -> str:
    """When the daemon's process began (its pid is 1 when it is the container's main process, so the pid cannot tell a restart)."""
    return run(container, "sh", "-c", f"stat -c %Y /proc/$(pgrep -x {daemon} | head -1)")


def release(container: str, daemon: str) -> str:
    return run(container, daemon, "-V")


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    out = benchctl("up", "--cpu-nodes", "1", "--gpu-nodes", "0", "--with", "accounting", "--slurm-version", FROM)
    assert out.returncode == 0, out.stdout + out.stderr
    assert wait_for(lambda: "idle" in run("login", "sinfo", "-h", "-o", "%t"), timeout=180), run("login", "sinfo")
    yield
    stack.down(say=lambda m: None)
    workbench.forget()


def test_every_stage_upgrades_only_its_daemon_and_the_job_survives(cluster):
    where = {"slurmdbd": "slurmdbd", "slurmctld": "slurmctld", "slurmd": "cn001"}  # daemon -> the container it runs in
    assert [release(c, d) for d, c in where.items()] == [f"slurm {FROM}"] * 3
    sub = scenario.submit_command({"node": "cn001", "duration": 900})
    job = subprocess.run(sub, capture_output=True, text=True, timeout=60, check=False).stdout.split()[-1]
    assert wait_for(lambda: run("slurmctld", "squeue", "-h", "-j", job, "-o", "%T") == "RUNNING", timeout=60)
    stepd = run("cn001", "pgrep", "-x", "slurmstepd")
    assert stepd
    for daemons, d in ((["slurmdbd"], "slurmdbd"), (["slurmctld", "slurmrestd"], "slurmctld"), (["cn001"], "slurmd")):
        began = started(where[d], d)
        o = scenario.execute(scenario.Step(0, "slurm-upgrade", {"to": TO, "daemons": daemons}))
        assert o.status == "ok", o.detail
        assert release(where[d], d) == f"slurm {TO}"
        assert started(where[d], d) != began  # a new process
        assert run("slurmctld", "squeue", "-h", "-j", job, "-o", "%T") == "RUNNING", f"job lost after upgrading {daemons}"
        assert run("cn001", "pgrep", "-x", "slurmstepd") == stepd, f"slurmstepd changed after upgrading {daemons}"
    run("slurmctld", "scancel", job)


def test_the_scenario_file_names_the_same_order():
    sc = yaml.safe_load((ROOT / "scenarios/slurm-upgrade-mid-job.yaml").read_text())
    assert [s["do"]["daemons"][0] for s in sc["steps"] if s["target"] == "slurm-upgrade"] == ["slurmdbd", "slurmctld", "cn001", "login"]
