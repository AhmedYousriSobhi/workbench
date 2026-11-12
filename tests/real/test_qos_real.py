"""Real QOS, priority and preemption on the live stack (issue 544, config lab A-6 and A-7).

Own cold start with the accounting part and a `qos:` in sim.yaml (ISOLATED_MODULES). Asserts on what slurmctld and slurmdbd say: `sacctmgr show qos`,
`squeue` pending reasons and `sacct` states, never on config text."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import compose, stack

ROOT = Path(__file__).resolve().parents[2]
CTL, LOGIN = "workbench-slurmctld-1", "workbench-login-1"
QOS = [{"name": "scavenger", "priority": 1, "max_gpus_per_user": 2}, {"name": "urgent", "priority": 1000, "preempt": ["scavenger"], "grace_time_s": 5}]


def run(c: str, *cmd: str, user: str | None = None) -> str:
    base = ["docker", "exec", *(["-u", user] if user else []), c]
    r = subprocess.run([*base, *cmd], capture_output=True, text=True, timeout=60, check=False)
    return r.stdout + r.stderr


def sbatch(user: str, qos: str, *extra: str, seconds: int = 600) -> str:
    out = run(LOGIN, "bash", "-lc", f"sbatch --parsable --qos={qos} --output=/dev/null --error=/dev/null --time=30 {' '.join(extra)} --wrap='sleep {seconds}'", user=user)
    assert out.strip().split(";")[0].isdigit(), out
    return out.strip().split(";")[0]


def squeue(job: str, fmt: str = "%T|%r") -> str:
    return run(CTL, "squeue", "-h", "-j", job, "-o", fmt).strip()


def sacct(job: str, fmt: str = "State") -> list[str]:
    return [ln.strip() for ln in run(CTL, "sacct", "-X", "-n", "-P", "-j", job, "--duplicates", "-o", fmt).splitlines() if ln.strip()]


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    sim["qos"] = QOS
    prof = compose.load_profile("min")
    prof["groups"] = [*prof["groups"], "accounting"]
    said: list[str] = []
    assert stack.up(sim, "min", say=said.append, available=64 * 1024**3, prof=prof), " | ".join(said[-4:])
    limits = lambda: run(CTL, "sacctmgr", "-n", "-P", "show", "qos", "format=name,maxtrespu")
    assert wait_for(lambda: "scavenger|gres/gpu=2" in limits(), timeout=150), f"the QOS limits never reached slurmdbd: {limits()!r}"
    assert wait_for(lambda: "cn001" in run(CTL, "sinfo", "-h", "-N", "-o", "%N") and "idle" in run(CTL, "sinfo", "-h", "-N", "-n", "cn001", "-o", "%t"), timeout=90)
    yield
    run(LOGIN, "scancel", "-u", "alice,bob")
    stack.down(say=lambda m: None)


def test_slurmdbd_has_the_qos_with_their_limits(cluster):
    rows = {r.split("|")[0]: r.split("|") for r in run(CTL, "sacctmgr", "-n", "-P", "show", "qos", "format=name,priority,maxtrespu,preempt,gracetime").splitlines()}
    assert rows["scavenger"][1:3] == ["1", "gres/gpu=2"]
    assert rows["urgent"][1] == "1000" and rows["urgent"][3] == "scavenger" and rows["urgent"][4] == "00:00:05"
    conf = run(CTL, "scontrol", "show", "config").replace(" ", "")
    assert "PreemptType=preempt/qos" in conf and "PriorityType=priority/multifactor" in conf and "limits,qos" in conf.split("AccountingStorageEnforce=")[1].split("\n")[0]


def test_a_job_over_the_gpu_limit_waits_with_the_real_reason(cluster):
    job = sbatch("bob", "scavenger", "--gres=gpu:3", "--partition=gpu")
    assert wait_for(lambda: "QOSMaxGRESPerUser" in squeue(job) or "MaxGRES" in squeue(job), timeout=40), squeue(job)
    run(LOGIN, "scancel", job, user="bob")


def test_an_urgent_job_requeues_a_scavenger_job(cluster):
    node = "cn001"
    low = sbatch("bob", "scavenger", f"--nodelist={node}", "--cpus-per-task=8")
    assert wait_for(lambda: squeue(low, "%T") == "RUNNING", timeout=40), squeue(low)
    high = sbatch("alice", "urgent", f"--nodelist={node}", "--cpus-per-task=8", seconds=60)
    assert wait_for(lambda: squeue(high, "%T") == "RUNNING", timeout=60), squeue(high)
    assert wait_for(lambda: any("PREEMPT" in s or "REQUEUE" in s for s in sacct(low)) or squeue(low, "%T") == "PENDING", timeout=30), (squeue(low), sacct(low))
    assert squeue(low, "%T") == "PENDING", squeue(low)  # requeued: back in the queue (a requeue holds a job for a moment: BeginTime)
    run(LOGIN, "scancel", low, high, user="bob")
