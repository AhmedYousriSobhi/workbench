"""The fake cmsh on the login node, run the way an SSH-based client runs it: a login shell over ssh, `cmsh -c "..."`."""
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import compose, scenario, stack

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / stack.SSH_DIR / "id_ed25519"


def ssh(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-i", str(KEY), "-p", str(compose.SSH_PORT), "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
                           "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
                           "alice@127.0.0.1", f"bash -lc '{command}'"], capture_output=True, text=True, timeout=30, check=False)


def cmsh(script: str) -> subprocess.CompletedProcess:
    return ssh(f'cmsh -c "{script}"')


@pytest.fixture(scope="module")
def head_node():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(lambda: cmsh("device; list").returncode == 0, timeout=60), cmsh("device; list").stderr
    yield
    stack.down(say=lambda m: None)


def test_device_list_has_the_head_node_and_every_simulated_node(head_node):
    out = cmsh("device; list").stdout
    assert re.search(r"^HeadNode\s+login\s+\S+\s+10\.141\.255\.254\s+internalnet\s+\[   UP   \]$", out, re.MULTILINE)
    for node in ("cn001", "cn004", "gpu001", "gpu002"):
        assert re.search(rf"^PhysicalNode\s+{node}\s", out, re.MULTILINE), out


def test_a_node_a_scenario_took_down_shows_down(head_node):
    step = scenario.Step(0, "bcm-mock", {"nodes": ["cn002"], "state": "DOWN"})
    assert scenario.execute(step).status == "ok"
    assert re.search(r"^cn002 \.+ \[  DOWN  \]$", cmsh("device; status").stdout, re.MULTILINE)
    assert re.search(r"^PhysicalNode\s+cn002\s.*\[  DOWN  \]$", cmsh("device; list").stdout, re.MULTILINE)
    scenario.execute(scenario.Step(0, "bcm-mock", {"nodes": ["cn002"], "state": "UP"}))
    assert wait_for(lambda: re.search(r"^cn002 \.+ \[   UP   \]$", cmsh("device; status").stdout, re.MULTILINE), timeout=10)


def test_other_read_only_queries(head_node):
    assert "gpu" in cmsh("category; list").stdout
    assert re.search(r"^slurm\s+cpu\s", cmsh("jobqueue; list").stdout, re.MULTILINE)
    assert "DeviceIsUp" in cmsh("monitoring; healthchecks; list").stdout


def test_mutating_and_unknown_commands_are_refused(head_node):
    r = cmsh("device; set cn001 category gpu")
    assert r.returncode == 1 and "read-only" in r.stderr
    assert cmsh("nosuchmode; list").returncode == 1


def test_cmsh_is_found_by_a_login_shell_and_a_probe(head_node):
    assert ssh("command -v cmsh").stdout.strip() == "/usr/local/bin/cmsh"
