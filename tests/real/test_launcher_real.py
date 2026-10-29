"""./scripts/opti on the live host: start -> the page answers -> start again is harmless -> stop leaves nothing."""
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPTI = str(ROOT / "scripts" / "opti")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def opti(*args, env=None, timeout=600):
    return subprocess.run([OPTI, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False,
                          env={**os.environ, **(env or {})})


def test_usage_and_status_need_no_stack():
    r = opti()
    assert r.returncode == 2 and "start" in r.stdout and "stop" in r.stdout
    s = opti("status")
    assert s.returncode == 0 and "governor" in s.stdout and "web page" in s.stdout


def test_start_serves_the_page_and_stop_cleans_up():
    port = str(free_port())
    env = {"OPTI_UI_PORT": port}
    try:
        r = opti("start", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "READY" in r.stdout and "Web page: http://127.0.0.1:" in r.stdout
        link = re.search(r"Web page: (\S+)", r.stdout).group(1)
        assert f":{port}/login?token=" in link
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        page = opener.open(link, timeout=10).read().decode()  # the login link signs in and serves the page
        assert "opti-sim" in page
        assert b"services" in opener.open(f"http://127.0.0.1:{port}/api/cluster", timeout=10).read()
        again = opti("start", env=env)  # idempotent
        assert again.returncode == 0 and "already running" in again.stdout, again.stdout
        st = opti("status", env=env)
        assert "containers running" in st.stdout and "web page : running" in st.stdout
        assert opti("open", env=env).stdout.strip().startswith("http://127.0.0.1:")
    finally:
        s = opti("stop", env=env)
    assert s.returncode == 0
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=3)
        raise AssertionError("the web page is still answering after stop")
    except (urllib.error.URLError, OSError):
        pass
    ps = subprocess.run(["docker", "ps", "--filter", "name=hpcsim", "--format", "{{.Names}}"],
                        capture_output=True, text=True, check=False)
    assert ps.stdout.strip() == ""
