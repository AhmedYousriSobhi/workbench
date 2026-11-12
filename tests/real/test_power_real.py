"""Slurm power save and reboot through the BMC farm on the live min stack (issue 545, config lab A-8 and A-9).

Asserts on the real components: slurmctld's own node state (`sinfo`) and the farm's Redfish answers, never on config text.
Observed behaviour that differs from the Slurm documentation belongs in docs/guides/power-and-reboot.md (not yet recorded: first run pending).
"""
import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, release, wait_for

ROOT = Path(__file__).resolve().parents[2]
CTLD = "workbench-slurmctld-1"
NODE = "cn002"
OTHER = "cn003"


def sh(*cmd: str):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)


def ctl(*args: str) -> str:
    r = sh("docker", "exec", CTLD, *args)
    assert r.returncode == 0, f"{args}: {(r.stderr or r.stdout).strip()}"
    return r.stdout


def state(node: str) -> str:
    return ctl("sinfo", "-h", "-N", "-n", node, "-o", "%T").split("\n")[0].strip()


def redfish(node: str, path: str = "Systems/1") -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:8000/{node}/redfish/v1/{path}", timeout=5) as r:
        return json.loads(r.read())


def put_back(node: str) -> None:
    sh("docker", "exec", CTLD, "scontrol", "power", "up", node)
    sh("docker", "exec", CTLD, "scontrol", "update", f"nodename={node}", "state=resume")


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: state(NODE) in ("idle", "mixed", "allocated"), timeout=60), f"{NODE} never became usable: {state(NODE)}"
    yield
    for n in (NODE, OTHER):  # leave the cluster as found
        put_back(n)
    release()


def test_power_down_gives_powered_down_and_a_redfish_off_then_power_up_returns_the_node(cluster):
    ctl("scontrol", "power", "down", NODE)
    assert wait_for(lambda: "powered" in state(NODE).lower() or "~" in state(NODE), timeout=60), f"state {state(NODE)!r}"
    assert wait_for(lambda: redfish(NODE)["PowerState"] == "Off", timeout=30), "the BMC still says On"
    ctl("scontrol", "power", "up", NODE)
    assert wait_for(lambda: redfish(NODE)["PowerState"] == "On", timeout=60)
    assert wait_for(lambda: state(NODE) in ("idle", "mixed", "allocated"), timeout=120), f"the node did not come back: {state(NODE)!r}"


def test_reboot_asap_cycles_the_node_through_the_bmc_and_it_resumes(cluster):
    before = len(redfish(OTHER, "Systems/1/LogServices/Log/Entries")["Members"])
    ctl("scontrol", "reboot", "ASAP", "nextstate=resume", "reason=test reboot", OTHER)
    messages = lambda: [m["Message"] for m in redfish(OTHER, "Systems/1/LogServices/Log/Entries")["Members"]]
    assert wait_for(lambda: "Reset ForceOff" in messages()[before:] and "Reset On" in messages()[before:], timeout=90), messages()
    assert wait_for(lambda: state(OTHER) in ("idle", "mixed", "allocated"), timeout=150), f"the node did not resume: {state(OTHER)!r}"


def test_the_node_health_check_still_runs_after_the_node_returns(cluster):
    """Issues 136, 151, 154: the health check and powered-off handling. A node that came back from power down is checked like any other:
    healthy (not drained) after it returns."""
    assert "drain" not in state(NODE).lower() and "drain" not in state(OTHER).lower()
