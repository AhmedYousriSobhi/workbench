"""NHC checks are managed live: a check added or removed with benchctl reaches a running node without a rebuild (#170)."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, exporter_up, wait_for

from benchctl import nhc, stack

ROOT = Path(__file__).resolve().parents[2]
NAME = "live-marker"


def docker(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", *cmd], capture_output=True, text=True, timeout=30, check=False)


def reason(node: str = "gpu001") -> str:
    return docker("workbench-login-1", "scontrol", "show", "node", node).stdout


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: "gpu001" in docker("workbench-login-1", "sinfo", "-h", "-N", "-t", "idle", "-o", "%N").stdout, timeout=90)
    assert wait_for(exporter_up, timeout=180), "the GPU exporter (it installs pyyaml at start) never answered"
    yield
    if NAME in [c.name for c in nhc.load()]:  # a failed test must not leave the check in the checkout
        nhc.remove(NAME)
        nhc.sync()
    stack.down(say=lambda m: None)


def test_the_nodes_mount_the_checks_and_a_new_check_drains_a_running_node_then_removing_it_resumes(cluster, tmp_path):
    assert "gpu-xid on" in docker("workbench-gpu001-1", "cat", "/etc/workbench/nhc/checks.conf").stdout  # mounted, not baked
    script = tmp_path / "marker.sh"
    # healthy for the validator's sample node (smoke001), unhealthy on the real one: proves the mounted file is what runs
    script.write_text('# check: live test marker\n[ "$node" = gpu001 ] && add "live marker"\nreturn 0\n')
    out = subprocess.run([str(ROOT / "scripts" / "benchctl"), "add", "check", NAME, "--script", str(script)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert wait_for(lambda: f"{NAME} on" in docker("workbench-gpu001-1", "cat", "/etc/workbench/nhc/checks.conf").stdout, timeout=10), "the mount did not follow the edit"
    assert wait_for(lambda: "NHC: live marker" in reason(), timeout=90), reason()  # the next 30 s run drains it
    out = subprocess.run([str(ROOT / "scripts" / "benchctl"), "remove", "check", NAME], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert wait_for(lambda: "NHC: live marker" not in reason(), timeout=90), reason()  # the periodic run resumes the drain it made
