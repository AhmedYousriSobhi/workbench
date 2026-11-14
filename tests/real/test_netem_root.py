"""Root-only: `tc netem` through the root governor service (issue 478, ADR 0056).

Run via scripts/real-test-root.sh (sudo). Creates one throwaway container shaped like a Slurm node (name and labels the governor
accepts; no `workbench.class` label, so no installed governor acts on it) and a throwaway governor on a temporary socket; both are removed.
"""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.geteuid() != 0, reason="needs root")
ROOT = Path(__file__).resolve().parents[2]
NODE = "cn9" + uuid.uuid4().hex[:2].translate(str.maketrans("abcdef", "012345"))  # cn9NN: never a real node of sim.yaml


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def wait_for(pred, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def ask(sock, req):
    with socket.socket(socket.AF_UNIX) as c:
        c.settimeout(20)
        c.connect(str(sock))
        c.sendall(json.dumps(req).encode() + b"\n")
        return json.loads(c.makefile().readline())


@pytest.fixture
def node():
    name = f"workbench-{NODE}-1"
    r = sh("docker", "run", "-d", "--rm", "--name", name, "--label", "workbench.node=compute", "--label", "com.docker.compose.project=workbench",
           "--memory", "50m", "busybox", "sleep", "300")
    assert r.returncode == 0, r.stderr
    pid = int(sh("docker", "inspect", "-f", "{{.State.Pid}}", name).stdout)
    yield name, pid
    sh("docker", "rm", "-f", name)


def qdisc(pid):
    return sh("nsenter", "-t", str(pid), "-n", "tc", "qdisc", "show", "dev", "eth0").stdout


def governor(tmp_path, *extra):
    sock = tmp_path / f"gov{len(extra)}.sock"
    p = subprocess.Popen([sys.executable, "-m", "governor", "--socket", str(sock), "--workdir", str(ROOT), "--interval", "0.5",
                          "--arrivals-flag", str(tmp_path / "arrivals"), *extra], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert wait_for(sock.exists)
    return p, sock


def test_set_ttl_clear_start_cleanup_and_bounds(node, tmp_path):
    _name, pid = node
    # a rule left by a crashed scenario: the governor must clear it at start
    assert sh("nsenter", "-t", str(pid), "-n", "tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "50ms").returncode == 0
    gov, sock = governor(tmp_path)
    try:
        assert wait_for(lambda: "netem" not in qdisc(pid)), "start must clear every netem rule"
        if ask(sock, {"cmd": "status"})["level"] > 1:
            pytest.skip("host memory pressure (L2+): netem sets only at L0-L1, which is the behaviour, not a failure")
        # bounds: one message per condition, nothing reaches tc
        r = ask(sock, {"cmd": "netem-set", "node": NODE, "delay_ms": 5000, "loss_pct": 50, "ttl_s": 9999})
        assert not r["ok"] and r["error"].count(";") == 2
        assert not ask(sock, {"cmd": "netem-set", "node": "../eth0", "delay_ms": 10, "ttl_s": 5})["ok"]
        assert not ask(sock, {"cmd": "netem-set", "node": "cn998", "delay_ms": 10, "ttl_s": 5})["ok"]  # not a container of this cluster
        # set, see it, let the TTL end it
        r = ask(sock, {"cmd": "netem-set", "node": NODE, "delay_ms": 200, "loss_pct": 5, "ttl_s": 3})
        assert r["ok"], r
        q = qdisc(pid)
        assert "netem" in q and "delay 200" in q and "loss 5%" in q
        assert ask(sock, {"cmd": "status"})["netem"][0]["node"] == NODE
        assert wait_for(lambda: "netem" not in qdisc(pid), 12), "TTL must remove the rule"
        # explicit clear
        assert ask(sock, {"cmd": "netem-set", "node": NODE, "delay_ms": 20, "ttl_s": 120})["ok"]
        assert "netem" in qdisc(pid)
        assert ask(sock, {"cmd": "netem-clear"})["ok"] and "netem" not in qdisc(pid)
        # the host's own interfaces are never touched
        assert "netem" not in sh("tc", "qdisc", "show").stdout
    finally:
        gov.terminate()
        gov.wait(timeout=10)


def test_governor_exit_clears_rules(node, tmp_path):
    _name, pid = node
    gov, sock = governor(tmp_path, "--refresh-interval", "30")
    try:
        if ask(sock, {"cmd": "status"})["level"] > 1:
            pytest.skip("host memory pressure (L2+)")
        assert ask(sock, {"cmd": "netem-set", "node": NODE, "delay_ms": 20, "ttl_s": 300})["ok"]
        assert "netem" in qdisc(pid)
    finally:
        gov.terminate()
        gov.wait(timeout=10)
    assert wait_for(lambda: "netem" not in qdisc(pid)), "exit must clear every rule"


def test_user_mode_governor_refuses_with_the_install_script(node, tmp_path):
    gov, sock = governor(tmp_path, "--user-mode", "--refresh-interval", "30", "--no-watchdog")
    try:
        r = ask(sock, {"cmd": "netem-set", "node": NODE, "delay_ms": 20, "ttl_s": 5})
        assert not r["ok"] and "install-governor.sh" in r["error"]
        assert ask(sock, {"cmd": "status"})["netem"] is None
    finally:
        gov.terminate()
        gov.wait(timeout=10)
