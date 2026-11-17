"""Config lab node containers on the live stack (issue 863): `apply lab` starts `lab-node-NNN` containers named like the production nodes,
Slurm shows them and the lab's partitions, a job runs on one, and `remove lab` stops them and restores the simulated partitions. Own cold
start (ISOLATED_MODULES): accounting on; it removes the lab and stops the stack at the end.
Not run by the agent: `./scripts/verify.sh --unit lab_nodes --clean`."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from benchctl import lab, lab_apply, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
NAME = "livenodes"


def ex(svc: str, *cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", f"workbench-{svc}-1", *cmd], capture_output=True, text=True, timeout=timeout, check=False)


def lab_containers() -> list[str]:
    r = subprocess.run(["docker", "ps", "--filter", f"label=workbench.lab={NAME}", "--format", "{{.Names}}"], capture_output=True, text=True, check=False)
    return sorted(r.stdout.split())


@pytest.fixture(scope="module")
def stack_up():
    os.chdir(ROOT)
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=["accounting"], cpu_nodes=2, gpu_nodes=0))
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        raise AssertionError(("ENVIRONMENT, not a code defect: " if "REFUSED by governor" in why else "") + f"stack.up failed: {why}")
    yield sim, prof
    if lab_apply.read_marker():
        lab_apply.remove(NAME, lab_apply.Env(sim=sim, running=lambda s: s in workbench.running_services()))
    stack.down(say=lambda m: None)
    workbench.forget()


def test_lab_nodes_run_and_go_away(stack_up, tmp_path):
    sim, _ = stack_up
    export = tmp_path / "export"
    shutil.copytree(FIX, export)
    lab.import_lab(NAME, export, replace=True)
    env = lab_apply.Env(sim=sim, running=lambda s: s in workbench.running_services(), say=lambda m: None)
    lab_apply.apply(NAME, "baseline", env)
    assert len(lab_containers()) == 6, lab_containers()
    deadline = time.time() + 90
    state = ""
    while time.time() < deadline:
        state = ex("slurmctld", "sinfo", "-h", "-N", "-o", "%N %t", "-p", "cpu").stdout
        if "cpu001 idle" in state:
            break
        time.sleep(3)
    assert "cpu001 idle" in state, state
    assert ex("slurmctld", "sinfo", "-h", "-o", "%P", "-p", "gpu").stdout.strip(), "the lab's gpu partition is missing"
    r = ex("slurmctld", "sbatch", "-p", "cpu", "-w", "cpu001", "--wrap", "hostname")
    assert r.returncode == 0, r.stderr
    lab_apply.remove(NAME, env)
    assert lab_containers() == []
    assert "cpu001" not in ex("slurmctld", "sinfo", "-h", "-N", "-o", "%N").stdout
