"""Simulated users with personas, failing and cancelled jobs and a daily rhythm, on the login node (issue 680).

Runs `benchctl load on --personas --day 120` against the shared min stack and reads what the users did from squeue
(finished jobs stay listed for MinJobAge). The test leaves the cluster as it found it: load off, its jobs cancelled."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, release, wait_for

from benchctl import cli, workload

ROOT = Path(__file__).resolve().parents[2]


def login(*cmd: str) -> str:
    r = subprocess.run(["docker", "exec", workload.LOGIN, *cmd], capture_output=True, text=True, timeout=30, check=False)
    return r.stdout


def jobs() -> list[tuple[str, str]]:
    """(job name, state) of every job squeue still lists, finished ones included."""
    rows = login("squeue", "-h", "-t", "all", "-o", "%j|%T").splitlines()
    return [tuple(r.split("|", 1)) for r in rows if "|" in r]  # type: ignore[misc]


@pytest.fixture(scope="module")
def sim():
    os.chdir(ROOT)
    s = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(s)
    yield s
    subprocess.run([str(ROOT / "scripts/benchctl"), "load", "off"], check=False, capture_output=True, timeout=60)
    login("scancel", "-u", "alice")
    login("scancel", "-u", "bob")
    release()


def test_personas_failures_cancellations_and_a_compressed_day(sim):
    from typer.testing import CliRunner

    out = CliRunner().invoke(cli.app, ["load", "on", "--personas", "--day", "120", "--rate", "60", "--duration", "10", "--seed", "1"])
    assert out.exit_code == 0 and "personas on" in out.output and "a day lasts 120 s" in out.output
    assert login("pgrep", "-f", workload.SIM_PATH).strip()  # one simulator process, started on the login node
    names = lambda: {n for n, _ in jobs()}
    assert wait_for(lambda: any(n.startswith("alice-train") for n in names()) and any(n.startswith("bob-mpi") for n in names()), timeout=120), jobs()
    assert wait_for(lambda: any(s == "FAILED" for _, s in jobs()), timeout=180), jobs()  # a persona job that exits 1
    assert wait_for(lambda: any(s == "CANCELLED" for _, s in jobs()), timeout=180), jobs()  # one the user scancelled
    pending = login("sh", "-c", "squeue -h -t PD | wc -l").strip()
    assert int(pending) <= 20 + 1  # the cap holds
    assert login("pgrep", "-fc", f"python3 {workload.SIM_PATH}").strip() == "1"  # no extra process per persona
    assert CliRunner().invoke(cli.app, ["load", "off"]).exit_code == 0
    assert wait_for(lambda: not login("pgrep", "-f", f"python3 {workload.SIM_PATH}").strip(), timeout=10)
