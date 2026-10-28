"""Live lifecycle of the compose stack (min profile): up, token works, scale, idempotent up, down.

Slow (about two minutes) and heavy (~3 GB); needs Docker and the built hpcsim images.
"""
import json
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml

from simctl import stack

ROOT = Path(__file__).resolve().parents[2]


def sinfo() -> str:
    r = subprocess.run(["docker", "exec", "hpcsim-slurmctld-1", "sinfo", "-h", "-o", "%N"],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip()


def project_containers() -> list[str]:
    r = subprocess.run(["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=hpcsim"],
                       capture_output=True, text=True, check=True)
    return r.stdout.split()


def annotate(token: str) -> int:
    req = urllib.request.Request(
        "http://127.0.0.1:3000/api/annotations", method="POST",
        data=json.dumps({"text": "real-test", "tags": ["governor"]}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.chdir(ROOT)
    s = yaml.safe_load((ROOT / "sim.yaml").read_text())
    yield s
    stack.down(say=lambda m: None)


def test_stack_lifecycle(sim):
    say = lambda m: None
    assert stack.up(sim, "min", say=say)
    assert sinfo() == "cn[001-004],gpu[001-002]"
    token = stack.TOKEN_FILE.read_text().strip()
    assert oct(stack.TOKEN_FILE.stat().st_mode)[-3:] == "600"
    assert annotate(token) == 200  # the governor's token really works
    assert stack.up(sim, "min", say=say)  # idempotent: second up on a running stack
    assert stack.scale(sim, "min", 2, say=say)
    assert sinfo() == "cn[001-002],gpu[001-002]"
    assert stack.scale(sim, "min", 3, say=say)  # grow again
    assert sinfo() == "cn[001-003],gpu[001-002]"
    assert stack.down(say=say)
    assert project_containers() == []
    # a fresh Grafana invalidates the old token; up must replace it
    assert stack.up(sim, "min", say=say)
    assert annotate(stack.TOKEN_FILE.read_text().strip()) == 200


def test_up_refuses_when_it_does_not_fit(sim):
    assert not stack.up(sim, "full", say=lambda m: None, available=2 * 1024**3)
    assert project_containers() == []
