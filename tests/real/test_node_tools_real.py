"""The basic Linux tools exist on a node and on the login node (issue 441). Not run by the agent: `./scripts/verify.sh --unit node_tools`."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, release

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ("ping", "ip", "ss", "dig", "nc", "traceroute", "tcpdump", "netstat", "curl", "ps", "top", "lsof", "strace", "vi")


@pytest.fixture(scope="module")
def stack():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    yield
    release()


@pytest.mark.parametrize("container", ["workbench-login-1", "workbench-cn001-1"])
def test_the_tools_are_there_and_ping_reaches_the_controller(stack, container):
    r = subprocess.run(["docker", "exec", container, "sh", "-c", f"for t in {' '.join(TOOLS)}; do command -v $t >/dev/null || echo missing $t; done"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.stdout.strip() == "" and r.returncode == 0, r.stdout + r.stderr
    p = subprocess.run(["docker", "exec", container, "ping", "-c", "1", "-W", "3", "slurmctld"], capture_output=True, text=True, timeout=30, check=False)
    assert p.returncode == 0, p.stdout + p.stderr
