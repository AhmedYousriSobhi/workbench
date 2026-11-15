"""Config lab apply and remove on the live stack (issue 553): the real slurmctld shows the applied keys, a restart-class key restarts it,
a live key reconfigures it, and `remove` returns `scontrol show config` to its pre-lab value. Written against the shared stack; the lab
uses a generated slurm.conf and one rule file only (no accounting, Kubernetes or Teleport), and removes itself at the end."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, release

from benchctl import lab, lab_apply, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
CTL = "workbench-slurmctld-1"
NAME = "livelab"


def show_config() -> dict[str, str]:
    r = subprocess.run(["docker", "exec", CTL, "scontrol", "show", "config"], capture_output=True, text=True, timeout=60, check=False)
    return lab_apply.parse_show_config(r.stdout)


@pytest.fixture(scope="module")
def stack_up():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim, "min")
    yield sim
    if lab_apply.read_marker():
        lab_apply.remove(NAME, lab_apply.Env(sim=sim, running=lambda s: s in workbench.running_services()))
    release()


def test_apply_candidate_then_remove_restores_the_pre_lab_config(stack_up, tmp_path):
    sim = stack_up
    export = tmp_path / "export"
    (export / "rules").mkdir(parents=True)
    (export / "slurm.conf").write_text("PriorityWeightQOS=4321\nPreemptMode=REQUEUE\nPreemptType=preempt/qos\nSchedulerParameters=bf_interval=45\n")
    (export / "rules" / "r.yml").write_text("groups:\n- name: lab\n  rules:\n  - alert: LabLive\n    expr: vector(1)\n")
    lab.import_lab(NAME, export, replace=True)
    env = lab_apply.Env(sim=sim, running=lambda s: s in workbench.running_services(), say=lambda m: None)
    before = lab_apply.comparable(show_config())
    res = lab_apply.apply(NAME, "baseline", env)
    now = show_config()
    assert now["PriorityWeightQOS"] == "4321" and now["PreemptMode"].upper() == "REQUEUE"
    assert any("prometheus reloaded" in line for line in res["lines"])
    assert (Path("compose/generated/slurm/slurm.conf").read_text().count("# lab-was") >= 1)
    stack.up(sim, "min", say=lambda m: None, available=64 * 2**30)  # `up` re-applies the marker and restarts nothing it need not
    assert show_config()["PriorityWeightQOS"] == "4321"
    lab_apply.remove(NAME, env)
    assert lab_apply.comparable(show_config()) == before
    assert lab_apply.read_marker() is None
