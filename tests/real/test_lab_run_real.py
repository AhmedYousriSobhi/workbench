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


def test_sacct_u_refuses_a_user_unknown_on_slurmctld(stack_up):
    """Issue 554 item 7, read from the Slurm 26.05.5 source: `sacct -u NAME` resolves NAME on the host it runs on. Confirms it live, and
    that `sacct -a` (what collect_sacct runs) works without the user."""
    r = ctl("sacct", "-X", "-P", "-u", "lab-u999", "-S", "now-60")
    assert r.returncode != 0 and "Invalid user id" in (r.stderr + r.stdout)
    assert ctl("sacct", "-a", "-X", "-P", "-S", "now-60").returncode == 0


def test_driver_replays_a_side_as_an_unlabelled_one_shot(stack_up, tmp_path):
    """Issue 554 step 3: the driver container starts, submits every job of the side (or counts it as rejected: the lab-uNNN users and
    lab nodes are not built yet, decision 5 and issue 863), reports progress, and leaves no container behind."""
    from benchctl import lab, lab_run, lab_steps
    root = tmp_path / "lab"
    lab.import_lab("prod-a", ROOT / "tests" / "fixtures" / "lab" / "full", root=root)
    run = lab_run.Run.create("prod-a", stack_up, root, ab=False)
    run.move("apply_candidate")
    run.move("replay_b")
    steps = lab_steps.RunSteps("prod-a", run.r["run"], root=root)
    reason = steps.replay("b", run)
    assert reason is None, reason
    n = run.r["jobs"]
    assert run.r["counts"]["submitted"] + run.r["rejected"]["b"] == n
    names = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True, check=True).stdout
    assert lab_steps.driver_name(run.r["run"], "b") not in names  # --rm
    assert not (ROOT / "compose" / "generated" / lab_steps.PROM_DIR / "slurm.json").exists()  # the lab_run label file is removed after the side
