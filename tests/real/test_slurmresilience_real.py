"""Real jobs under daemon trouble (#220, ADR 0025): what slurmstepd, the job and the node do when slurmd or slurmctld
restarts, slurmd stays down, or the node is drained under a running job.

The expectations come from Slurm's documentation. Where the live stack disagrees the assertion message says what was
expected and what was seen: the finding is recorded in docs/guides/slurm-resilience.md, the test is not bent.
"""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, exporter_up, wait_for

from benchctl import scenario, stack

ROOT = Path(__file__).resolve().parents[2]
NODE = "cn001"


def sh(*cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def ctl(*cmd: str) -> str:
    return sh("docker", "exec", scenario.SLURMCTLD, *cmd).stdout.strip()


def on_node(*cmd: str) -> subprocess.CompletedProcess:
    return sh("docker", "exec", f"workbench-{NODE}-1", *cmd)


def pids(name: str) -> list[str]:
    return on_node("pgrep", "-x", name).stdout.split()


def job_state(job: str) -> str:
    return ctl("squeue", "-h", "-j", job, "-o", "%T")


def node_state() -> str:
    return scenario.node_state(NODE)


def submit(duration: int = 600, node: str = NODE, **extra) -> str:
    cmd = scenario.submit_command({"node": node, "duration": duration, **extra})
    r = sh(*cmd)
    assert r.returncode == 0, r.stderr
    job = r.stdout.split()[-1]
    assert wait_for(lambda: job_state(job) == "RUNNING", timeout=60), f"job {job} never ran: {ctl('squeue')}"
    assert wait_for(lambda: pids("slurmstepd"), timeout=20), "no slurmstepd for the running job"
    return job


def run_steps(do: dict) -> None:
    o = scenario.execute(scenario.Step(0, "slurm", do))
    assert o.status == "ok", o.detail


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: "idle" in node_state(), timeout=90)
    yield
    stack.down(say=lambda m: None)


@pytest.fixture(autouse=True)
def clean(cluster):
    yield
    run_steps({"slurmd_start": [NODE], "cancel": True})
    wait_for(lambda: "idle" in node_state() or "drain" not in node_state() and "down" not in node_state(), timeout=90)
    ctl("scontrol", "update", f"nodename={NODE}", "state=resume")


def test_slurmd_is_not_pid_1_so_it_can_restart_without_the_container():
    assert on_node("cat", "/proc/1/comm").stdout.strip() != "slurmd", "slurmd is PID 1: a restart would take the container along"
    assert pids("slurmd")


def test_slurmd_restart_leaves_slurmstepd_and_the_job_running():
    job = submit()
    step_before, slurmd_before = pids("slurmstepd"), pids("slurmd")
    run_steps({"slurmd_restart": [NODE]})
    assert wait_for(lambda: pids("slurmd") and pids("slurmd") != slurmd_before, timeout=30), "slurmd did not come back"
    assert pids("slurmstepd") == step_before, f"expected the same slurmstepd {step_before}, saw {pids('slurmstepd')}"
    assert job_state(job) == "RUNNING", f"expected RUNNING after a slurmd restart, saw {job_state(job) or 'no job'}"


def test_slurmd_down_for_a_while_marks_the_node_not_responding_and_it_returns_with_the_job():
    job = submit()
    step_before = pids("slurmstepd")
    run_steps({"slurmd_stop": [NODE]})
    assert wait_for(lambda: not pids("slurmd"), timeout=15)
    assert pids("slurmstepd") == step_before, "slurmstepd must outlive slurmd"
    assert wait_for(lambda: "*" in node_state() or "down" in node_state(), timeout=90), f"node still {node_state()} with slurmd down"
    run_steps({"slurmd_start": [NODE]})
    assert wait_for(lambda: pids("slurmd"), timeout=30)
    assert wait_for(lambda: "down" not in node_state() and "*" not in node_state(), timeout=90), f"node stayed {node_state()}"
    assert job_state(job) == "RUNNING", f"expected the job RUNNING after slurmd returned, saw {job_state(job) or 'no job'}"


def test_slurmctld_restart_keeps_jobs_and_nodes():
    job = submit()
    step_before = pids("slurmstepd")
    run_steps({"slurmctld_restart": True})
    assert wait_for(lambda: job_state(job) == "RUNNING", timeout=120), f"job {job} after slurmctld restart: {job_state(job) or 'unknown'}"
    assert pids("slurmstepd") == step_before
    assert wait_for(lambda: "idle" not in node_state() and "down" not in node_state(), timeout=60), node_state()  # allocated or mixed


def test_a_simulated_gpu_job_keeps_its_gpus_busy_across_a_slurmctld_restart():
    assert wait_for(exporter_up, timeout=180), "the GPU exporter never answered"
    job = submit(node="gpu001", gpus=2)
    run_steps({"slurmctld_restart": True})
    assert wait_for(lambda: job_state(job) == "RUNNING", timeout=120), job_state(job)


def test_drain_under_a_running_job_lets_it_finish():
    job = submit(duration=40)
    ctl("scontrol", "update", f"nodename={NODE}", "state=drain", "reason=resilience-test")
    assert wait_for(lambda: "drain" in node_state(), timeout=15)
    assert job_state(job) == "RUNNING", "a drain must not touch the running job"
    assert wait_for(lambda: job_state(job) == "", timeout=90), "the job never finished"
    assert wait_for(lambda: "drained" in node_state(), timeout=30), f"expected drained after the job ended, saw {node_state()}"


def test_the_shipped_scenarios_run_and_trace(cluster):
    lines: list[str] = []
    for name in ("slurmd-restart-mid-job", "drain-mid-job"):
        sc = scenario.load(name)
        report = scenario.run(sc, speed=3, say=lines.append)
        assert report.ok, "\n".join(lines)


def test_the_governor_runs_the_steps_and_serves_the_trace_for_the_page(cluster):
    """The page has no Docker: it asks the governor. Needs a governor with --workdir (benchctl start runs one)."""
    from benchctl import checks
    sock = os.environ.get("WORKBENCH_GOV_SOCKET", checks.default_socket())
    try:
        res = checks.governor_request({"cmd": "slurm-trace", "nodes": [NODE], "since": 30}, sock)
    except OSError as e:
        pytest.skip(f"no governor to ask: {e}")
    assert res.get("ok"), res
    data = res["trace"]
    assert NODE in data["nodes"] and "jobs" in data and "workbench-slurmctld-1" in data["logs"]
