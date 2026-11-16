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
from helpers import bring_up, release, skip_no_governor, wait_for

from benchctl import checks

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ui():
    if not Path(checks.GOV_SOCKET).exists():
        skip_no_governor()
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "webui", str(port)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    proc.stdout.readline()  # the page prints its address; the token is only in its 0600 file (a terminal-only link, #346)
    token = (ROOT / "compose/generated/secrets/ui_token").read_text().strip()
    base = f"http://127.0.0.1:{port}"
    h = {"Authorization": f"Bearer {token}", "X-Requested-With": "workbench"}

    def up() -> bool:
        try:
            return api(base, h, "/api/status").get("ok") is not None
        except OSError:
            return False

    assert wait_for(up, timeout=20)
    yield base, h, proc
    proc.terminate()
    proc.wait(timeout=10)
    release()


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


def cli_run(base, *args):
    """`benchctl scenario run` against this page (its port in BENCHCTL_UI_PORT; the token is the same 0600 file)."""
    env = {**os.environ, "BENCHCTL_UI_PORT": base.rsplit(":", 1)[1]}
    return subprocess.run([str(ROOT / "scripts/benchctl"), "scenario", "run", *args], check=False, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def test_a_cli_run_shows_on_the_page_with_its_real_results(ui):
    """ADR 0086 phase 4: the CLI posts its steps; the page keeps them as a run from the CLI. Both ssh steps are CLI only on the page."""
    base, h, _ = ui
    r = cli_run(base, "login-node-down", "--speed", "10")
    assert r.returncode == 0 and "web page" not in r.stdout, r.stdout[-600:] + r.stderr[-300:]
    st = api(base, h, "/api/scenarios/run")
    assert (st["name"], st["source"], st["running"], st["ok"]) == ("login-node-down", "cli", False, True)
    assert [(s["status"], s["page"], s["t"] is not None) for s in st["steps"]] == [("ok", False, True), ("ok", False, True)]


def test_a_cli_run_while_the_page_plays_is_refused_once_and_still_runs(ui):
    base, h, _ = ui
    assert api(base, h, "/api/scenarios/jira-outage/run?speed=1", "POST") == {"started": "jira-outage"}
    try:
        r = cli_run(base, "jira-429")
        assert r.returncode == 0 and r.stdout.count("web page: this run is not shown there: jira-outage is already running on the page") == 1, r.stdout[-600:]
        assert api(base, h, "/api/scenarios/run")["source"] == "page"
    finally:
        api(base, h, "/api/scenarios/stop", "POST")
        assert wait_for(lambda: not api(base, h, "/api/scenarios/run")["running"])
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8080/sim/failure", data=json.dumps({"mode": None}).encode(), method="POST"), timeout=5).close()
