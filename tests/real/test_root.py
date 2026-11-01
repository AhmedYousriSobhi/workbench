"""Root-only checks: Docker container cgroups and the governor as a real systemd service.

Run via scripts/real-test-root.sh (sudo).
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

from governor.executors import Executor, discover_docker
from governor.memlock import MitigationPath
from governor.planner import Action

pytestmark = pytest.mark.skipif(os.geteuid() != 0, reason="needs root")
ROOT = Path(__file__).resolve().parents[2]


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def wait_for(pred, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def container():
    name = f"workbench-test-{uuid.uuid4().hex[:6]}"
    r = sh("docker", "run", "-d", "--rm", "--name", name, "--label", "workbench.class=P4",
           "--memory", "200m", "busybox", "sh", "-c", "head -c 50000000 /dev/zero | tail; sleep 300")
    assert r.returncode == 0, r.stderr
    yield name
    sh("docker", "rm", "-f", name)


def frozen(cg: Path) -> str:
    return dict(ln.split() for ln in (cg / "cgroup.events").read_text().splitlines())["frozen"]


def test_docker_cgroup_freeze_throttle_reclaim(container):
    groups, cgs = discover_docker()
    cg = cgs[container]
    assert cg.exists(), cg
    ex = Executor(groups, cgs, MitigationPath())
    ex(Action("freeze", container, 3))
    assert wait_for(lambda: frozen(cg) == "1")
    ex(Action("thaw", container, 0))
    assert wait_for(lambda: frozen(cg) == "0")
    before = int((cg / "memory.current").read_text())
    ex(Action("throttle", container, 2))
    high = int((cg / "memory.high").read_text())
    assert abs(high - int(before * 0.8)) < 4096  # kernel rounds memory.high down to a page
    ex(Action("unthrottle", container, 0))
    assert (cg / "memory.high").read_text().strip() == "max"
    ex(Action("reclaim", container, 3))
    assert sh("docker", "inspect", "-f", "{{.State.Running}}", container).stdout.strip() == "true"


def test_governor_as_systemd_service(tmp_path):
    unit = f"workbench-test-gov-{uuid.uuid4().hex[:6]}"
    sock = f"/run/{unit}/gov.sock"
    r = sh("systemd-run", f"--unit={unit}", "-p", "OOMScoreAdjust=-900", "-p", "MemoryLow=64M",
           "-p", "LimitMEMLOCK=infinity", "-p", "Restart=always", "-p", f"RuntimeDirectory={unit}",
           "-p", f"WorkingDirectory={ROOT}", sys.executable, "-m", "governor", "--socket", sock,
           "--require-lock", "--interval", "0.5")
    assert r.returncode == 0, r.stderr
    try:
        assert wait_for(lambda: Path(sock).exists())
        pid = int(sh("systemctl", "show", "-p", "MainPID", "--value", unit).stdout)
        assert Path(f"/proc/{pid}/oom_score_adj").read_text().strip() == "-900"
        lck = [ln for ln in Path(f"/proc/{pid}/status").read_text().splitlines() if "VmLck" in ln]
        assert int(lck[0].split()[1]) > 1000
        assert oct(Path(sock).stat().st_mode & 0o777) == "0o660"
        # Restart=always: kill it hard and it must come back with its lock
        os.kill(pid, 9)
        assert wait_for(lambda: sh("systemctl", "show", "-p", "MainPID", "--value", unit).stdout.strip()
                        not in ("0", str(pid)), 20)
    finally:
        sh("systemctl", "stop", unit)
        sh("systemctl", "reset-failed", unit)


def test_nf1_memory_hog_stopped_in_ladder_order_without_oom(tmp_path):
    """NF-1 (root: needs cgroup.freeze/cgroup.kill on the Docker cgroup): a container growing toward its memory.max is escalated and stopped before the OOM killer.

    The hog fills a tmpfs (charged to the container cgroup) at ~30 MB/s under a 200 MB cap. The
    governor watches that cgroup only, so the host is never put under pressure.
    """
    name = f"workbench-test-{uuid.uuid4().hex[:6]}"
    hog = ("i=0; while true; do dd if=/dev/zero of=/t/f$i bs=1M count=10 2>/dev/null; "
           "i=$((i+1)); sleep 0.3; done")
    subprocess.run(["docker", "run", "-d", "--name", name, "--label", "workbench.class=P4",
                    "--memory", "200m", "--memory-swap", "200m", "--tmpfs", "/t:size=400m",
                    "busybox", "sh", "-c", hog], check=True, capture_output=True)
    try:
        _, cgs = discover_docker()
        cg = cgs[name]
        sock = tmp_path / "gov.sock"
        thr = {"l0_mem": 100 * 1024**2, "l1_mem": 80 * 1024**2, "l4_mem": 25 * 1024**2}
        gov = subprocess.Popen(
            [sys.executable, "-m", "governor", "--watch-cgroup", str(cg), "--socket", str(sock),
             "--interval", "0.1", "--thresholds", json.dumps(thr),
             "--arrivals-flag", str(tmp_path / "arrivals")],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            assert wait_for(sock.exists)

            def status():
                with socket.socket(socket.AF_UNIX) as c:
                    c.connect(str(sock))
                    c.sendall(b'{"cmd":"status"}\n')
                    return json.loads(c.makefile().readline())

            assert wait_for(lambda: not running(name), 40)
            st = status()
            audit = st["audit"]
            i_warn = next(i for i, m in enumerate(audit) if "stop-arrivals" in m)
            i_stop = next(i for i, m in enumerate(audit) if f"stop {name}" in m)
            assert i_warn < i_stop  # warned before it stopped anything
            assert st["level"] == 4 and gov.poll() is None  # governor alive, at L4
            assert (tmp_path / "arrivals").exists()
            oom = subprocess.run(["docker", "inspect", "-f", "{{.State.OOMKilled}}", name],
                                 capture_output=True, text=True, check=False).stdout.strip()
            assert oom == "false"  # stopped by the governor, not the kernel OOM killer
        finally:
            gov.terminate()
            gov.wait(timeout=10)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def running(name: str) -> bool:
    r = sh("docker", "inspect", "-f", "{{.State.Running}}", name)
    return r.stdout.strip() == "true"
