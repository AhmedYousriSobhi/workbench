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


def running_services() -> list[str]:
    r = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.project=hpcsim",
                        "--format", '{{.Label "com.docker.compose.service"}}'],
                       capture_output=True, text=True, check=True)
    return sorted(r.stdout.split())


def cn_running() -> list[str]:
    return [s for s in running_services() if s.startswith("cn")]


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
    assert cn_running() == ["cn001", "cn002"]  # the containers really stopped, not just slurm.conf
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


def test_governor_admission_and_scale_via_socket(sim, tmp_path, monkeypatch):
    """GV-9 end to end: a live governor refuses under pressure and scales the stack via its socket."""
    from helpers import ask, signals, start_governor, stop_governor, wait_for
    gov, sig, sock = start_governor(tmp_path, "--no-watchdog", "--workdir", str(ROOT))
    monkeypatch.setenv("HPCSIM_GOV_SOCKET", str(sock))
    say = lambda m: None
    try:
        assert stack.up(sim, "min", say=say)  # admitted by the live governor
        assert sinfo() == "cn[001-004],gpu[001-002]"
        assert ask(sock, {"cmd": "scale", "group": "compute", "n": 2})["started"]
        assert wait_for(lambda: sinfo() == "cn[001-002],gpu[001-002]", timeout=90)
        assert cn_running() == ["cn001", "cn002"]
        assert wait_for(lambda: any("scale=2: done" in a for a in ask(sock, {"cmd": "status"})["audit"]))
        signals(sig, avail_gb=4, some=25)  # sustained pressure -> L2
        assert wait_for(lambda: ask(sock, {"cmd": "status"})["level"] >= 2, timeout=30)
        assert not ask(sock, {"cmd": "scale", "group": "compute", "n": 4})["ok"]
        assert not ask(sock, {"cmd": "admit", "budget_mb": 100})["ok"]
        said: list[str] = []
        assert not stack.scale(sim, "min", 4, say=said.append), said  # simctl honours the refusal
        assert sinfo() == "cn[001-002],gpu[001-002]" and cn_running() == ["cn001", "cn002"]
    finally:
        stop_governor(gov)
