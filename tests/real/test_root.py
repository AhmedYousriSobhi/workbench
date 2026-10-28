"""Root-only checks: Docker container cgroups and the governor as a real systemd service.

Run via scripts/real-test-root.sh (sudo).
"""
import os
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
    name = f"hpcsim-test-{uuid.uuid4().hex[:6]}"
    r = sh("docker", "run", "-d", "--rm", "--name", name, "--label", "hpcsim.class=P4",
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
    unit = f"hpcsim-test-gov-{uuid.uuid4().hex[:6]}"
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
