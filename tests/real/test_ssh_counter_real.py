"""The SSH session counter of the login node in the live min stack (issue 213, ADR 0088): a real `ssh` is counted, a bare TCP connect and a
refused login are not, the page's flow feed turns the rise into an event on `ssh:login`, and the port is on 127.0.0.1 only.

Written for `./scripts/verify.sh --unit ssh_counter`; not run when it was added (the owner runs the live tests). The jump host's counter
needs the `jump` part and is covered by the unit tests; add it here when a live run with `--with jump` is wanted.
"""
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, release, wait_for

from benchctl import compose, stack

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / stack.SSH_DIR / "id_ed25519"
STATS = f"http://127.0.0.1:{compose.SSH_PORT + compose.COUNTER_OFFSET}/sim/stats"


def ssh(*cmd: str, user: str = "alice") -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-i", str(KEY), "-p", str(compose.SSH_PORT), "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
                           "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@127.0.0.1", *cmd],
                          capture_output=True, text=True, timeout=30, check=False)


def stats() -> dict:
    with urllib.request.urlopen(STATS, timeout=3) as r:
        return json.load(r)


@pytest.fixture(scope="module")
def login():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(lambda: ssh("true").returncode == 0, timeout=45), ssh("true").stderr
    assert wait_for(lambda: stats() is not None, timeout=15)
    yield sim
    release()


def test_a_real_ssh_login_is_counted_with_its_source_and_nothing_else(login):
    before = stats()["requests"]
    assert ssh("true").returncode == 0
    assert wait_for(lambda: stats()["requests"] == before + 1, timeout=10), stats()
    doc = stats()
    assert doc["by_source"] and sum(doc["by_source"].values()) == doc["requests"]
    assert "alice" not in json.dumps(doc)  # a number per source address: no user name, no command


def settled() -> int:
    """The count once no login has landed for two seconds: in the shared stack an earlier module's session can still be logged."""
    last = stats()["requests"]
    for _ in range(15):
        time.sleep(2)
        now = stats()["requests"]
        if now == last:
            break
        last = now
    return last


def test_a_tcp_probe_and_a_refused_login_are_not_counted(login):
    before = settled()
    with socket.create_connection(("127.0.0.1", compose.SSH_PORT), timeout=3):
        pass  # what a liveness check does: connect, never authenticate
    assert ssh("true", user="mallory").returncode != 0
    assert ssh("true", user="root").returncode != 0
    assert not wait_for(lambda: stats()["requests"] != before, timeout=3)


def test_the_flow_feed_makes_an_event_on_the_direct_ssh_line(login):
    from webui.flow import Flow

    f = Flow(ssh=lambda: {"ssh:login": STATS}, min_interval=0)
    f.poll()
    assert f.since(0)["reachable"]["ssh:login"] is True
    assert ssh("true").returncode == 0
    assert wait_for(lambda: (f.poll(), any(e["to"] == "ssh:login" and e["from"] == "clients" for e in f.since(0)["events"]))[1], timeout=10)


def test_sshd_still_logs_to_docker_logs_and_the_counter_port_is_local_only(login):
    out = subprocess.run(["docker", "logs", "--tail", "50", "workbench-login-1"], capture_output=True, text=True, check=False)
    assert "Accepted publickey for alice" in out.stdout + out.stderr  # the counter repeats sshd's lines
    ports = subprocess.run(["docker", "port", "workbench-login-1"], capture_output=True, text=True, check=False).stdout
    assert f"127.0.0.1:{compose.SSH_PORT + compose.COUNTER_OFFSET}" in ports and "0.0.0.0" not in ports
