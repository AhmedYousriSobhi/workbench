"""Tests against the real host: user-delegated cgroups, Docker, a real governor process."""
import json
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from governor.executors import CGROUP_ROOT, Executor, discover_docker
from governor.memlock import MitigationPath
from governor.planner import Action, Group

ROOT = Path(__file__).resolve().parents[2]


def wait_for(pred, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def hog_cgroup():
    """A real process holding ~100 MB inside a user-delegated cgroup with memory control."""
    code = (
        "import time\nb=bytearray(100*1024*1024)\nb[::4096]=b'x'*len(b[::4096])\n"
        "print(open('/proc/self/cgroup').read().strip(),flush=True)\ntime.sleep(60)\n"
    )
    p = subprocess.Popen(
        ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=400M", sys.executable, "-c", code],
        stdout=subprocess.PIPE, text=True)
    line = p.stdout.readline().strip()
    cg = CGROUP_ROOT / line.split("::/")[1]
    yield cg
    p.kill()  # systemd-run --scope execs the command in this pid
    p.wait(timeout=10)


def events(cg: Path, key: str) -> str:
    for ln in (cg / "cgroup.events").read_text().splitlines():
        if ln.startswith(key):
            return ln.split()[1]
    return ""


def test_real_freeze_throttle_reclaim(hog_cgroup):
    cg = hog_cgroup
    ex = Executor([Group("hog", 4)], {"hog": cg}, MitigationPath())
    before = int((cg / "memory.current").read_text())
    assert before > 50 * 1024**2
    ex(Action("freeze", "hog", 3))
    assert wait_for(lambda: events(cg, "frozen") == "1")
    ex(Action("thaw", "hog", 0))
    assert wait_for(lambda: events(cg, "frozen") == "0")
    ex(Action("throttle", "hog", 2))
    high = int((cg / "memory.high").read_text())
    assert 0 < high < before
    ex(Action("unthrottle", "hog", 0))
    assert (cg / "memory.high").read_text().strip() == "max"
    ex(Action("reclaim", "hog", 3))  # succeeds; pages move to swap/zram if configured


def test_real_mlockall_in_subprocess():
    code = (
        "from governor.memlock import lock_memory, locked_kb, raise_memlock_limit\n"
        "raise_memlock_limit(); lock_memory(); print(locked_kb())"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                         check=True)
    assert int(out.stdout) > 1000


@pytest.fixture
def container():
    name = f"hpcsim-test-{uuid.uuid4().hex[:6]}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--label", "hpcsim.class=P4",
                    "busybox", "sleep", "300"], check=True, capture_output=True)
    yield name
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def running(name: str) -> bool:
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip() == "true"


def test_real_discovery_and_stop(container):
    groups, cgs = discover_docker()
    assert Group(container, 4) in groups
    assert cgs[container].name.startswith("docker-")
    ex = Executor([Group(container, 4)], {}, MitigationPath())
    ex(Action("stop", container, 4))
    assert wait_for(lambda: not running(container))


def test_real_governor_process_end_to_end(container, tmp_path):
    """Real governor process: driven L0 -> L4 via a signals file, stops the real container."""
    sig = tmp_path / "sig.json"
    sock = tmp_path / "gov.sock"
    sig.write_text(json.dumps({"mem_available": 4 * 1024**3, "psi_some": 0, "psi_full": 0}))
    gov = subprocess.Popen(
        [sys.executable, "-m", "governor", "--signals-file", str(sig), "--socket", str(sock),
         "--interval", "0.2"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert wait_for(sock.exists)

        def ask(req):
            with socket.socket(socket.AF_UNIX) as c:
                c.connect(str(sock))
                c.sendall(json.dumps(req).encode() + b"\n")
                return json.loads(c.makefile().readline())

        st = ask({"cmd": "status"})
        assert st["level"] == 0 and container in st["groups"] and not st["dry_run"]
        assert running(container)
        sig.write_text(json.dumps({"mem_available": 4 * 1024**3, "psi_some": 40, "psi_full": 9}))
        assert wait_for(lambda: ask({"cmd": "status"})["level"] == 4)
        assert wait_for(lambda: not running(container))
        audit = ask({"cmd": "status"})["audit"]
        assert any(f"stop {container}" in m for m in audit)
        assert ask({"cmd": "drop-database"})["ok"] is False
    finally:
        gov.terminate()
        gov.wait(timeout=10)
    assert not sock.exists()  # SIGTERM cleanup


def test_real_governor_flags_privileged_container_then_resolves(tmp_path):
    """The live watchdog reports an over-privileged sim container and clears it once removed."""
    sig = tmp_path / "sig.json"
    sock = tmp_path / "gov.sock"
    sig.write_text(json.dumps({"mem_available": 8 * 1024**3, "psi_some": 0, "psi_full": 0}))
    clean, bad = (f"hpcsim-test-{uuid.uuid4().hex[:6]}" for _ in range(2))

    def start(name, *extra):
        subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--label", "hpcsim.class=P4",
                        "--memory", "30m", *extra, "busybox", "sleep", "300"],
                       check=True, capture_output=True)

    start(clean)
    gov = subprocess.Popen(
        [sys.executable, "-m", "governor", "--dry-run", "--signals-file", str(sig), "--socket", str(sock),
         "--interval", "0.2", "--watch-interval", "0.5"], cwd=ROOT, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    try:
        assert wait_for(sock.exists)

        def violations():
            with socket.socket(socket.AF_UNIX) as c:
                c.connect(str(sock))
                c.sendall(b'{"cmd": "status"}\n')
                return json.loads(c.makefile().readline())["violations"]

        time.sleep(1.5)
        assert violations() == []  # unprivileged and limited: nothing to report
        start(bad, "--cap-add", "SYS_ADMIN")
        assert wait_for(lambda: any(bad in v and "SYS_ADMIN" in v for v in violations()))
        subprocess.run(["docker", "rm", "-f", bad], capture_output=True, check=False)
        assert wait_for(lambda: violations() == [])
    finally:
        gov.terminate()
        gov.wait(timeout=10)
        for n in (clean, bad):
            subprocess.run(["docker", "rm", "-f", n], capture_output=True, check=False)
