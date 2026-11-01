"""Rootless governor (ADR 0010) under real conditions, run as an ordinary user: no sudo, no cgroup writes.

1. NF-1 without root: a real container fills its memory cap; the governor (watching that cgroup only, so the host is
   never squeezed) warns, freezes through Docker, then stops it before the kernel OOM killer.
2. The ladder on the live stack: real containers are really paused (L3) and stopped (L4), pauses are undone when calm, stops stay until started, and
   the memory.high throttle is skipped instead of failing.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml
from helpers import ask, bring_up, signals, start_governor, stop_governor, wait_for

from benchctl import stack
from governor.executors import discover_docker

ROOT = Path(__file__).resolve().parents[2]
FAST = json.dumps({"sustain_s": 0.5, "calm_s": 1, "min_dwell_s": 0.3, "l5_after_s": 1000})
pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="the point is to run without root")


def inspect(name: str, field: str) -> str:
    return subprocess.run(["docker", "inspect", "-f", field, name], capture_output=True, text=True,
                          check=False).stdout.strip()


def test_nf1_memory_hog_is_paused_then_stopped_without_root_and_without_oom(tmp_path):
    name = f"workbench-test-{uuid.uuid4().hex[:6]}"
    hog = ("i=0; while true; do dd if=/dev/zero of=/t/f$i bs=1M count=10 2>/dev/null; "
           "i=$((i+1)); sleep 0.3; done")
    subprocess.run(["docker", "run", "-d", "--name", name, "--label", "workbench.class=P4", "--memory", "200m",
                    "--memory-swap", "200m", "--tmpfs", "/t:size=400m", "busybox", "sh", "-c", hog],
                   check=True, capture_output=True)
    try:
        _, cgs = discover_docker()
        sock = tmp_path / "gov.sock"
        thr = {"l0_mem": 100 * 1024**2, "l1_mem": 80 * 1024**2, "l4_mem": 25 * 1024**2}
        gov = subprocess.Popen([sys.executable, "-m", "governor", "--user-mode", "--watch-cgroup", str(cgs[name]),
                                "--socket", str(sock), "--interval", "0.1", "--thresholds", json.dumps(thr),
                                "--arrivals-flag", str(tmp_path / "arrivals")],
                               cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            assert wait_for(sock.exists)
            assert wait_for(lambda: inspect(name, "{{.State.Running}}") == "false", 60), "the hog was never stopped"
            audit = ask(sock, {"cmd": "status"})
            st, log = audit["audit"], " | ".join(audit["audit"])
            order = [next(i for i, m in enumerate(st) if key in m) for key in ("stop-arrivals", f"freeze {name}", f"stop {name}")]
            assert order == sorted(order), log  # warned, then paused, then stopped
            assert audit["level"] == 4 and audit["mode"] == "user" and gov.poll() is None
            assert (tmp_path / "arrivals").exists()
            assert inspect(name, "{{.State.OOMKilled}}") == "false"  # the governor won, not the kernel
        finally:
            gov.terminate()
            _, err = gov.communicate(timeout=10)
        assert b"Traceback" not in err, err.decode()[-800:]
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.chdir(ROOT)
    s = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(s)
    yield s
    stack.down(say=lambda m: None)


def test_ladder_really_pauses_and_stops_live_containers_and_recovers(sim, tmp_path):
    gov, sig, sock = start_governor(tmp_path, "--user-mode", "--no-watchdog", "--thresholds", FAST,
                                    "--refresh-interval", "1")
    cn, ctl, bmc = "workbench-cn001-1", "workbench-slurmctld-1", "workbench-bmc-farm-1"
    try:
        assert wait_for(lambda: cn in ask(sock, {"cmd": "status"})["groups"], 30)
        high = lambda n: subprocess.run(["docker", "exec", n, "cat", "/sys/fs/cgroup/memory.high"],
                                        capture_output=True, text=True, check=False).stdout.strip()

        signals(sig, avail_gb=4, some=25)  # L2: throttle, which rootless mode skips rather than fails
        assert wait_for(lambda: ask(sock, {"cmd": "status"})["level"] == 2, 30)
        assert wait_for(lambda: any("throttle" in m for m in ask(sock, {"cmd": "status"})["audit"]), 15)
        assert high(cn) in ("max", "")  # untouched: no memory.high write without root
        assert inspect(cn, "{{.State.Running}}") == "true"

        signals(sig, avail_gb=4, some=35)  # L3: freeze -> docker pause
        assert wait_for(lambda: inspect(cn, "{{.State.Paused}}") == "true", 30)
        assert inspect(ctl, "{{.State.Paused}}") == "false"  # P1 is never frozen at L3
        assert not ask(sock, {"cmd": "admit", "budget_mb": 10})["ok"]  # admission refused while squeezed

        signals(sig, avail_gb=8)  # calm: the pause is undone (a freeze is reversible)
        assert wait_for(lambda: inspect(cn, "{{.State.Paused}}") == "false" and inspect(cn, "{{.State.Running}}") == "true", 60)
        assert wait_for(lambda: ask(sock, {"cmd": "status"})["level"] == 0, 60)
        assert ask(sock, {"cmd": "admit", "budget_mb": 10})["ok"]

        signals(sig, avail_gb=0.5, full=10)  # L4: stop (SIGKILL, like cgroup.kill in the root path)
        assert wait_for(lambda: inspect(cn, "{{.State.Running}}") == "false", 30)
        assert inspect(ctl, "{{.State.Running}}") == "true" and inspect(bmc, "{{.State.Running}}") == "true"

        signals(sig, avail_gb=8)  # calm again: a stop is destructive and is never undone automatically (by design)
        assert wait_for(lambda: ask(sock, {"cmd": "status"})["level"] == 0, 60)
        assert inspect(cn, "{{.State.Running}}") == "false"
        assert ask(sock, {"cmd": "start", "target": cn})["ok"]  # an operator (or benchctl) brings it back
        assert wait_for(lambda: inspect(cn, "{{.State.Running}}") == "true", 30)
        st = ask(sock, {"cmd": "status"})
        assert st["mode"] == "user" and not any("FAILED" in m for m in st["audit"]), st["audit"]
    finally:
        err = stop_governor(gov)
    assert b"Traceback" not in err, err.decode()[-800:]
