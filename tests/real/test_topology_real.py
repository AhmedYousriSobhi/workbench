"""Slurm topology on the live stack (issue 543): the real `scontrol show topology` matches sim.yaml, and a switch step drains exactly one leaf's nodes.

Own cold start (a sim.yaml with `topology:`), so the module is in ISOLATED_MODULES and ends with its own stack.down."""
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import scenario, stack, topology

ROOT = Path(__file__).resolve().parents[2]
CTL = "workbench-slurmctld-1"


def ctl(*cmd: str) -> str:
    r = subprocess.run(["docker", "exec", CTL, *cmd], capture_output=True, text=True, timeout=60, check=False)
    return r.stdout + r.stderr


def expand(hostlist: str) -> list[str]:
    return ctl("scontrol", "show", "hostnames", hostlist).split()


def shown() -> dict[str, list[str]]:
    """{switch: nodes} as the real controller reports it."""
    out = {}
    for line in ctl("scontrol", "show", "topology").splitlines():
        m = re.match(r"SwitchName=(\S+) Level=0 .*Nodes=(\S+)", line)
        if m:
            out[m.group(1)] = expand(m.group(2))
    return out


def drained(node: str) -> str:
    return ctl("scontrol", "show", "node", node)


@pytest.fixture(scope="module")
def sim():
    os.chdir(ROOT)
    cfg = yaml.safe_load((ROOT / "sim.yaml").read_text())
    cfg["topology"] = {"plugin": "tree", "spines": 1, "leaves_per_spine": 2}
    bring_up(cfg)
    assert wait_for(lambda: "cn001" in ctl("sinfo", "-h", "-N", "-o", "%N"), timeout=60)
    yield cfg
    stack.down(say=lambda m: None)


def step(cfg, **do):
    out = scenario.execute(scenario.Step(0, "switch", do))
    assert out.status == "ok", out.detail


def test_scontrol_show_topology_matches_sim_yaml(sim):
    assert wait_for(lambda: bool(shown()), timeout=30)
    assert shown() == topology.leaves(sim)


def test_a_leaf_down_drains_exactly_its_nodes_and_up_resumes_only_those(sim):
    leaves = topology.leaves(sim)
    inside, outside = leaves["leaf1"], leaves["leaf2"]
    ctl("scontrol", "update", f"nodename={outside[0]}", "state=drain", "reason=by hand")  # not the switch's doing: must stay drained
    step(sim, leaf="leaf1", state="down")
    assert wait_for(lambda: all("DRAIN" in drained(n) for n in inside), timeout=20)
    assert all("switch leaf1 down" in drained(n) for n in inside)
    assert "by hand" in drained(outside[0]) and all("DRAIN" not in drained(n) for n in outside[1:])
    step(sim, leaf="leaf1", state="up")
    assert wait_for(lambda: all("DRAIN" not in drained(n) for n in inside), timeout=20)
    assert "DRAIN" in drained(outside[0])  # only the leaf's own nodes came back
    ctl("scontrol", "update", f"nodename={outside[0]}", "state=resume")
