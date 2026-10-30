"""./scripts/benchctl-launch on the live host: start -> the page answers -> start again is harmless -> stop leaves nothing."""
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = str(ROOT / "scripts" / "benchctl-launch")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(*args, env=None, timeout=600):
    return subprocess.run([LAUNCHER, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False,
                          env={**os.environ, **(env or {})})


def _freeze_and_thaw_a_real_container(checks, usock):
    """The rootless governor really pauses and unpauses a running simulation container through Docker."""
    import time
    name = "workbench-bmc-farm-1"
    deadline = time.time() + 20
    while time.time() < deadline and name not in checks.governor_request({"cmd": "status"}, usock)["groups"]:
        time.sleep(0.5)  # the inventory refresh picks up containers started after the governor
    def paused():
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Paused}}", name], capture_output=True, text=True,
                             check=False)
        return out.stdout.strip() == "true"
    assert checks.governor_request({"cmd": "freeze", "target": name}, usock)["ok"] and paused()
    assert checks.governor_request({"cmd": "thaw", "target": name}, usock)["ok"] and not paused()


def test_usage_and_status_need_no_stack():
    r = launch()
    assert r.returncode == 2 and "start" in r.stdout and "stop" in r.stdout
    s = launch("status")
    assert s.returncode == 0 and "governor" in s.stdout and "web page" in s.stdout


def test_start_serves_the_page_and_stop_cleans_up(tmp_path):
    """Also the no-sudo guarantee: a sudo that fails the test if called is first on PATH, the governor is rootless."""
    port = str(free_port())
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "sudo").write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path}/sudo-called\nexit 1\n")
    (shim / "sudo").chmod(0o755)
    usock = f"{os.environ.get('XDG_RUNTIME_DIR') or '/tmp/workbench-' + str(os.getuid())}/workbench/gov.sock"
    env = {"BENCHCTL_UI_PORT": port, "BENCHCTL_GOVERNOR": "user", "PATH": f"{shim}:{os.environ['PATH']}"}
    try:
        r = launch("start", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "READY" in r.stdout and "Web page: http://127.0.0.1:" in r.stdout
        assert "running as you" in r.stdout and "not reachable" not in r.stdout, r.stdout
        from benchctl import checks
        st = checks.governor_request({"cmd": "status"}, usock)  # the rootless governor answers on the user socket
        assert st["ok"] and st["mode"] == "user"
        _freeze_and_thaw_a_real_container(checks, usock)
        link = re.search(r"Web page: (\S+)", r.stdout).group(1)
        assert f":{port}/login?token=" in link
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        page = opener.open(link, timeout=10).read().decode()  # the login link signs in and serves the page
        assert "workbench" in page
        assert b"services" in opener.open(f"http://127.0.0.1:{port}/api/cluster", timeout=10).read()
        again = launch("start", env=env)  # idempotent
        assert again.returncode == 0 and "already running" in again.stdout, again.stdout
        st = launch("status", env=env)
        assert "containers running" in st.stdout and "web page : running" in st.stdout
        assert launch("open", env=env).stdout.strip().startswith("http://127.0.0.1:")
    finally:
        s = launch("stop", env=env)
    assert s.returncode == 0
    assert not (tmp_path / "sudo-called").exists(), "start/stop called sudo"
    assert not os.path.exists(usock), "the rootless governor was left running"
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=3)
        raise AssertionError("the web page is still answering after stop")
    except (urllib.error.URLError, OSError):
        pass
    ps = subprocess.run(["docker", "ps", "--filter", "name=workbench", "--format", "{{.Names}}"],
                        capture_output=True, text=True, check=False)
    assert ps.stdout.strip() == ""
