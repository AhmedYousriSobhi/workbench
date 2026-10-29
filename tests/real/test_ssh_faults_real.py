"""SSH fault scenarios against the live login node: changed host key, down, hung, session limit.

Each fault is applied with the scenario engine's own step executor and observed the way a client sees it.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from simctl import compose, scenario, stack

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / stack.SSH_DIR / "id_ed25519"
PORT = str(compose.SSH_PORT)


def ssh(*cmd: str, known_hosts: str = "/dev/null", strict: str = "no", timeout: float = 30, opts: tuple[str, ...] = ()):
    return subprocess.run(["ssh", "-i", str(KEY), "-p", PORT, "-o", "IdentitiesOnly=yes", "-o", f"StrictHostKeyChecking={strict}",
                           "-o", f"UserKnownHostsFile={known_hosts}", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
                           *opts, "alice@127.0.0.1", *cmd], capture_output=True, text=True, timeout=timeout, check=False)


def fingerprint() -> str:
    r = subprocess.run(["ssh-keyscan", "-p", PORT, "-t", "ed25519", "127.0.0.1"], capture_output=True, text=True, timeout=15, check=False)
    return r.stdout.strip().split()[-1] if r.stdout.strip() else ""


def step(**do):
    out = scenario.execute(scenario.Step(0, "ssh", do))
    assert out.status == "ok", out.detail


@pytest.fixture(scope="module")
def login():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(lambda: ssh("true").returncode == 0, timeout=60), ssh("true").stderr
    yield
    step(reset=True)
    stack.down(say=lambda m: None)


@pytest.fixture(autouse=True)
def restore(login):
    yield
    step(reset=True)
    assert wait_for(lambda: ssh("true").returncode == 0, timeout=30)


def test_changed_host_key_is_refused_by_a_client_that_pinned_the_old_one(login, tmp_path):
    pinned = str(tmp_path / "known_hosts")
    assert ssh("true", known_hosts=pinned, strict="accept-new").returncode == 0  # first contact pins the key
    before = fingerprint()
    step(rotate_host_key=True)
    assert wait_for(lambda: fingerprint() not in ("", before), timeout=15)
    r = ssh("true", known_hosts=pinned, strict="yes")
    assert r.returncode != 0 and "host key" in r.stderr.lower() and "changed" in r.stderr.lower(), r.stderr
    step(reset=True)
    assert wait_for(lambda: fingerprint() == before, timeout=15)  # the original key is back
    assert ssh("true", known_hosts=pinned, strict="yes").returncode == 0


def test_down_refuses_connections_and_up_restores_the_same_host_key(login):
    before = fingerprint()
    step(state="down")
    r = ssh("true")
    assert r.returncode != 0 and ("refused" in r.stderr.lower() or "closed" in r.stderr.lower()), r.stderr
    step(state="up")
    assert wait_for(lambda: ssh("true").returncode == 0, timeout=45)
    assert fingerprint() == before


def test_hung_node_accepts_nothing_then_thaws(login):
    step(state="hung")
    start = time.monotonic()
    try:
        r = ssh("true", timeout=12)
        assert r.returncode != 0
    except subprocess.TimeoutExpired:
        pass  # a client waiting on a frozen server never gets an answer
    assert time.monotonic() - start >= 3  # it hung, it did not refuse at once
    step(state="up")
    assert wait_for(lambda: ssh("true").returncode == 0, timeout=30)


def test_session_limit_refuses_the_second_channel_on_one_connection(login, tmp_path):
    step(max_sessions=1)
    sock = str(tmp_path / "mux")
    master = ("-o", "ControlMaster=yes", "-o", f"ControlPath={sock}", "-o", "ControlPersist=20")
    assert wait_for(lambda: ssh("true", opts=master).returncode == 0, timeout=15)
    mux = ("-o", f"ControlPath={sock}", "-o", "ControlMaster=no")
    first = subprocess.Popen(["ssh", "-i", str(KEY), "-p", PORT, *mux, "alice@127.0.0.1", "sleep", "6"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    second = ssh("true", opts=mux)
    first.terminate()
    subprocess.run(["ssh", "-p", PORT, "-o", f"ControlPath={sock}", "-O", "exit", "alice@127.0.0.1"], capture_output=True, check=False)
    # ssh's own mux client then retries on a fresh connection (exit 0); the refusal is what a library client would see
    assert "refused by peer" in second.stderr, second.stderr
    step(reset=True)
