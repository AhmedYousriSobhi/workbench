"""A scenario attached to a config lab run on the live stack (issue 554, `run lab --scenario`): the frozen scenario's steps act on the
lab node containers (drain through slurmctld by the Slurm name, power off as a paused `workbench-lab-node-NNN-1`), the player's clock
holds while the governor's pause flag exists, and the heal unpauses the container and resumes the nodes. The player is driven with
`sync` calls instead of the replay driver, so this checks the scenario's effect on real containers, not a whole A/B run. Own cold start
(ISOLATED_MODULES): accounting on; it removes the lab and stops the stack at the end.
Not run by the agent: `./scripts/verify.sh --unit lab_scenario --clean`."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from benchctl import lab, lab_apply, lab_scenario, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
NAME = "livescen"


def ctl(*cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "workbench-slurmctld-1", *cmd], capture_output=True, text=True, timeout=timeout, check=False)


def paused(container: str) -> bool:
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Paused}}", container], capture_output=True, text=True, check=False)
    return r.stdout.strip() == "true"


def node_state(node: str) -> str:
    return ctl("sinfo", "-h", "-N", "-n", node, "-o", "%t").stdout.strip()


def wait_for(fn, timeout: float = 90, every: float = 2):
    end = time.time() + timeout
    while time.time() < end:
        if v := fn():
            return v
        time.sleep(every)
    return fn()


@pytest.fixture(scope="module")
def lab_up(tmp_path_factory):
    os.chdir(ROOT)
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=["accounting"], cpu_nodes=1, gpu_nodes=0))
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        raise AssertionError(("ENVIRONMENT, not a code defect: " if "REFUSED by governor" in why else "") + f"stack.up failed: {why}")
    export = tmp_path_factory.mktemp("export") / "x"
    shutil.copytree(FIX, export)
    lab.import_lab(NAME, export, replace=True)
    env = lab_apply.Env(sim=sim, running=lambda s: s in workbench.running_services(), say=lambda m: None)
    lab_apply.apply(NAME, "baseline", env)
    assert wait_for(lambda: "idle" in node_state("cpu001")), node_state("cpu001")
    yield sim
    if lab_apply.read_marker():
        lab_apply.remove(NAME, env)
    stack.down(say=lambda m: None)
    workbench.forget()


def test_scenario_steps_act_on_the_lab_nodes_and_the_heal_undoes_them(lab_up, tmp_path):
    sim = lab_up
    scen = tmp_path / "scenarios"
    scen.mkdir()
    (scen / "live.yaml").write_text(yaml.safe_dump({"name": "live", "steps": [
        {"at": "0s", "target": "slurm", "do": {"drain": ["first cpu node"], "reason": "lab scenario live"}},
        {"at": "30s", "target": "slurm", "do": {"power_off": ["second cpu node"]}}],
        "expect": [{"slurm.node_state": {"node": "first cpu node", "state": "drain", "within": "20s"}}]}))
    doc = lab_scenario.freeze(NAME, "live", sim, 1.0, 600, scenarios=scen)
    container = f"workbench-{doc['steps'][1]['do']['power_off'][0]}-1"
    assert container == "workbench-lab-node-002-1"
    flag = {"paused": False}
    pl = lab_scenario.Player(doc, lambda: flag["paused"], lambda: False)
    pl.sync(0.0)
    assert wait_for(lambda: "drain" in node_state("cpu001"), 30), node_state("cpu001")  # scontrol by the Slurm (production) name
    flag["paused"] = True  # L2: the clock holds, the power-off at 30 s must wait
    time.sleep(40)
    assert not paused(container), "a step ran while the replay clock was paused"
    flag["paused"] = False
    assert wait_for(lambda: paused(container), 60), "power_off did not pause the lab node's container"
    res = pl.finish(timeout=90)
    assert [s["status"] for s in res["steps"]] == ["ok", "ok"], res
    assert res["expects"][0]["status"] == "ok", res
    healed = lab_scenario.heal(doc)
    assert healed["failed"] == 0, healed
    assert not paused(container)
    assert wait_for(lambda: "drain" not in node_state("cpu001"), 60), node_state("cpu001")
