"""Config lab snapshot and restore on the live stack (issue 554 step 2): a job submitted after the snapshot is gone from slurmctld
and from accounting after the restore, the restored state hashes equal the snapshot's, run A's accounting is collected first, and
the JWT key still works. Own cold start (ISOLATED_MODULES): a selection with accounting and one CPU node; it stops the stack at the end.
Not run by the agent: `./scripts/verify.sh --unit lab_run --clean`."""
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from benchctl import lab_snapshot, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
CTL = "workbench-slurmctld-1"


def ctl(*cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", CTL, *cmd], capture_output=True, text=True, timeout=timeout, check=False)


def wait_ctld(timeout: int = 90) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if ctl("scontrol", "ping").returncode == 0:
            return
        time.sleep(2)
    raise AssertionError("slurmctld did not answer after the restore")


@pytest.fixture(scope="module")
def stack_up():
    os.chdir(ROOT)
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=["accounting"], cpu_nodes=1, gpu_nodes=0))
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        raise AssertionError(("ENVIRONMENT, not a code defect: " if "REFUSED by governor" in why else "") + f"stack.up failed: {why}")
    assert {"slurmctld", "slurmdbd", "mariadb"} <= workbench.running_services(), workbench.running_services()
    yield sim
    stack.down(say=lambda m: None)
    workbench.forget()


def test_restore_puts_back_the_state_of_the_snapshot(stack_up, tmp_path):
    c = lab_snapshot.Cluster()
    dest = tmp_path / "snap"
    snap = lab_snapshot.snapshot(dest, c)
    assert snap["spool_files"] > 0 and snap["db_sha256"]
    wait_ctld()
    user = stack_up["users"][0]["name"]
    r = ctl("su", "-s", "/bin/sh", user, "-c", "sbatch --parsable -J lab-live -t 1 --wrap 'sleep 1'")
    assert r.returncode == 0, r.stderr
    job = r.stdout.strip().split(";")[0]
    end = time.time() + 120
    while time.time() < end and job not in ctl("sacct", "-n", "-X", "-P", "-j", job, "-o", "JobID").stdout:
        time.sleep(3)
    assert job in ctl("sacct", "-n", "-X", "-P", "-j", job, "-o", "JobID").stdout, "the job never reached accounting"
    collected = lab_snapshot.collect_sacct([user], 600, c)["sacct.txt"]  # run A's accounting, before the restore
    assert "lab-live" in collected
    res = lab_snapshot.restore(dest, c)
    assert res["spool_match"] and res["db_match"], res
    wait_ctld()
    assert job not in ctl("sacct", "-n", "-X", "-P", "-j", job, "-o", "JobID").stdout  # accounting is back to the snapshot
    assert "lab-live" not in ctl("squeue", "-h", "-o", "%j").stdout
    assert ctl("scontrol", "token", "username=" + user).returncode == 0  # the JWT key was kept, not restored
    lab_snapshot.discard(dest)
    assert not dest.exists()
