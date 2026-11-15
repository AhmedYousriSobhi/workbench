"""Config lab apply and remove on the live stack (issue 553): the real slurmctld shows the applied keys, a restart-class key restarts it,
a live key reconfigures it, and `remove` returns `scontrol show config` to its pre-lab value. Own cold start (ISOLATED_MODULES, like test_lab_real): a workbench selection with no optional parts (Prometheus always runs): no
accounting, Kubernetes or Teleport (the lab here is a generated slurm.conf and one rule file); it removes itself and stops the stack at the end.
Not run by the agent: `./scripts/verify.sh --unit lab_apply --clean`."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml

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
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=[], cpu_nodes=2, gpu_nodes=0))
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        raise AssertionError(("ENVIRONMENT, not a code defect: " if "REFUSED by governor" in why else "") + f"stack.up failed: {why}")
    assert {"slurmctld", "prometheus"} <= workbench.running_services(), workbench.running_services()
    yield sim, prof
    if lab_apply.read_marker():
        lab_apply.remove(NAME, lab_apply.Env(sim=sim, running=lambda s: s in workbench.running_services()))
    stack.down(say=lambda m: None)
    workbench.forget()


def test_apply_candidate_then_remove_restores_the_pre_lab_config(stack_up, tmp_path):
    sim, prof = stack_up
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
    stack.up(sim, "workbench", say=lambda m: None, available=64 * 2**30, prof=prof)  # `up` re-applies the marker and restarts nothing it need not
    assert show_config()["PriorityWeightQOS"] == "4321"
    lab_apply.remove(NAME, env)
    assert lab_apply.comparable(show_config()) == before
    assert lab_apply.read_marker() is None
