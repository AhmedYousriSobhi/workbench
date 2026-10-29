"""The Web UI process against the real governor and the live stack: login, telemetry, a scenario that really bites, RSS."""
import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from simctl import checks, stack

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ui():
    if not Path(checks.GOV_SOCKET).exists():
        pytest.skip("no standing governor (sudo ./scripts/install-governor.sh)")
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    assert stack.up(sim, "min", say=lambda m: None, available=64 * 1024**3)
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    token = proc.stdout.readline().split("token=")[1].strip()
    base = f"http://127.0.0.1:{port}"
    h = {"Authorization": f"Bearer {token}", "X-Requested-With": "opti-sim"}

    def up() -> bool:
        try:
            return api(base, h, "/api/status").get("ok") is not None
        except OSError:
            return False

    assert wait_for(up, timeout=20)
    yield base, h, proc
    proc.terminate()
    proc.wait(timeout=10)
    stack.down(say=lambda m: None)


def api(base, h, path, method="GET"):
    r = urllib.request.Request(base + path, headers=h, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())


def test_reports_the_real_governor(ui):
    base, h, _ = ui
    st = api(base, h, "/api/status")
    assert st["ok"] and st["level"] in range(6) and st["mem_available"] > 0


def test_a_scenario_played_from_the_ui_really_changes_the_mock(ui):
    base, h, _ = ui
    assert wait_for(lambda: urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=5).status == 200, timeout=30)
    assert api(base, h, "/api/scenarios/jira-429/run?speed=1", "POST") == {"started": "jira-429"}
    assert wait_for(lambda: not api(base, h, "/api/scenarios/run")["running"])
    state = api(base, h, "/api/scenarios/run")
    assert state["ok"] and state["steps"][0]["status"] == "ok"
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen("http://127.0.0.1:8080/rest/api/2/issue/X-1", timeout=5)
    assert e.value.code == 429  # the UI's click reached the live jira-mock


def test_ui_stays_under_the_memory_budget(ui):
    _, _, proc = ui
    line = next(ln for ln in Path(f"/proc/{proc.pid}/status").read_text().splitlines() if ln.startswith("VmRSS"))
    rss_kb = int(line.split()[1])
    assert rss_kb < 50 * 1024, f"{rss_kb / 1024:.0f} MB"  # SPEC W-1
