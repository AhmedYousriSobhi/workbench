import json
import socket
import threading
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

pytest.importorskip("starlette")
pytest.importorskip("uvicorn")
import uvicorn

from governor import control
from webui import __main__ as launcher
from webui.app import create_app, governor_status

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "test-token-123"
STATUS = {"ok": True, "level": 2, "dry_run": False, "mem_available": 3 * 2**30, "psi_some": 4.5, "psi_full": 0.1,
          "groups": ["compute"], "violations": [], "audit": ["L2: throttle p4"]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ui(tmp_path):
    sock = str(tmp_path / "gov.sock")
    gov = control.serve(sock, lambda cmd, req: STATUS, lambda: {"compute"})
    scen = tmp_path / "scenarios"
    scen.mkdir()
    (scen / "quick.yaml").write_text(
        "name: quick\ndescription: d\nsteps:\n  - {at: 0s, target: slurm, do: {drain: [cn001]}}\n"
        "  - {at: 0s, target: k8s, do: {x: 1}}\n")
    (scen / "slow.yaml").write_text("name: slow\ndescription: d\nsteps:\n  - {at: 0s, target: k8s, do: {a: 1}}\n"
                                    "  - {at: 1h, target: k8s, do: {b: 2}}\n")
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, sock, scen, ROOT / "sim.yaml", interval=0.05),
                                           host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    gov.shutdown()


def req(base, path, method="GET", auth=True, headers=None):
    h = {"Authorization": f"Bearer {TOKEN}"} if auth else {}
    h.update(headers or {})
    r = urllib.request.Request(base + path, method=method, headers=h, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()


def test_everything_needs_the_token(ui):
    assert req(ui, "/api/status", auth=False)[0] == 401
    assert req(ui, "/", auth=False)[0] == 401
    assert req(ui, "/api/status", headers={"Authorization": "Bearer nope"})[0] == 401
    code, body = req(ui, "/api/status")
    assert code == 200 and json.loads(body)["level"] == 2


def test_login_sets_a_strict_cookie_and_rejects_a_wrong_token(ui):
    assert req(ui, "/login?token=wrong", auth=False)[0] == 401

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(HTTPError) as e:
        opener.open(f"{ui}/login?token={TOKEN}")
    assert e.value.code == 303
    cookie = e.value.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower()


def test_page_is_served_and_has_no_external_resources(ui):
    code, body = req(ui, "/")
    assert code == 200 and b"workbench" in body
    assert b"http://" not in body and b"https://" not in body  # works offline, loads nothing third-party


def test_sse_streams_governor_status(ui):
    r = urllib.request.Request(ui + "/api/events", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(r, timeout=5) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        first = resp.readline().decode()
        resp.readline()
        second = resp.readline().decode()
    assert first.startswith("data: ") and json.loads(first[6:])["audit"] == ["L2: throttle p4"]
    assert second.startswith("data: ")  # more than one event: it keeps streaming


def test_governor_down_is_reported_not_crashed(tmp_path):
    out = governor_status(str(tmp_path / "missing.sock"))
    assert out["ok"] is False and "not reachable" in out["error"]


def test_scenario_list_and_run_skips_slurm_without_docker(ui):
    names = [s["name"] for s in json.loads(req(ui, "/api/scenarios")[1])]
    assert names == ["quick", "slow"]
    assert req(ui, "/api/scenarios/quick/run", "POST")[0] == 403  # no X-Requested-With: CSRF guard
    code, _ = req(ui, "/api/scenarios/quick/run", "POST", headers={"X-Requested-With": "workbench"})
    assert code == 200
    deadline = time.time() + 5
    state = {}
    while time.time() < deadline:
        state = json.loads(req(ui, "/api/scenarios/run")[1])
        if not state["running"]:
            break
        time.sleep(0.05)
    assert state["ok"] is True and [s["status"] for s in state["steps"]] == ["skipped", "skipped"]
    assert "Docker" in state["steps"][0]["detail"]  # the UI never drains Slurm itself


def test_stop_ends_a_running_scenario_and_unknown_is_404(ui):
    h = {"X-Requested-With": "workbench"}
    assert req(ui, "/api/scenarios/nope/run", "POST", headers=h)[0] == 404
    assert req(ui, "/api/scenarios/slow/run", "POST", headers=h)[0] == 200
    assert req(ui, "/api/scenarios/quick/run", "POST", headers=h)[0] == 409  # one at a time
    assert req(ui, "/api/scenarios/stop", "POST", headers=h)[0] == 200
    deadline = time.time() + 5
    state = {}
    while time.time() < deadline:
        state = json.loads(req(ui, "/api/scenarios/run")[1])
        if not state["running"]:
            break
        time.sleep(0.05)
    assert state["stopped"] is True and state["steps"][1]["status"] == "pending"
    assert req(ui, "/api/scenarios/slow/run?speed=0", "POST", headers=h)[0] == 422


def test_token_file_is_created_private_and_stable(tmp_path):
    path = tmp_path / "secrets" / "ui_token"
    first = launcher.load_token(path)
    assert len(first) >= 24 and oct(path.stat().st_mode)[-3:] == "600"
    assert launcher.load_token(path) == first


def test_launcher_prints_a_login_link_and_serves_with_the_token(monkeypatch, tmp_path, capsys):
    ran = {}
    monkeypatch.setattr(launcher, "TOKEN_FILE", tmp_path / "secrets" / "ui_token")
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kw: ran.update(kw=kw, app=app))
    monkeypatch.setattr(launcher.sys, "argv", ["webui", "9123"])
    launcher.main()
    out = capsys.readouterr().out
    token = launcher.load_token(tmp_path / "secrets" / "ui_token")
    assert out.strip() == f"workbench UI: http://127.0.0.1:9123/login?token={token}"
    assert ran["kw"] == {"host": "127.0.0.1", "port": 9123, "log_level": "warning"}  # localhost only (W-8)


def test_cluster_endpoints_need_auth_csrf_and_validate(ui):
    assert req(ui, "/api/cluster", auth=False)[0] == 401
    code, body = req(ui, "/api/cluster")  # no stack in unit tests: every node is "unknown", not a 500
    snap = json.loads(body)
    assert code == 200 and len(snap["nodes"]) == 6 and {n["state"] for n in snap["nodes"]} == {"unknown"}
    assert req(ui, "/api/nodes/cn001/clear", "POST")[0] == 403  # CSRF header missing
    h = {"X-Requested-With": "workbench"}
    assert req(ui, "/api/nodes/nope/clear", "POST", headers=h)[0] == 422
    assert req(ui, "/api/nodes/cn001/gpu-xid", "POST", headers=h)[0] == 422
    code, body = req(ui, "/api/nodes/cn001/overheat", "POST", headers=h)
    assert code == 200 and json.loads(body)["results"][0]["status"] == "failed"  # mocks not running: reported, not crashed


def test_page_has_the_app_shell(ui):
    body = req(ui, "/")[1].decode()
    for needle in ('class="skip"', '<main id="main"', 'aria-label="Governor"', 'aria-label="Operate"',
                   'id="theme"', 'id="strip"', 'id="attn"', 'id="list"', 'id="flow"', 'prefers-reduced-motion'):
        assert needle in body, needle


def test_page_recovery_hint_needs_no_sudo():
    """ADR 0010: a dead governor is started with `benchctl start`; the sudo service is only an optional upgrade."""
    page = (ROOT / "webui" / "static" / "index.html").read_text()
    offline = next(line for line in page.splitlines() if "Governor not reachable" in line)
    assert "./scripts/benchctl start" in offline and offline.index("benchctl start") < offline.index("sudo")


def test_help_wiki_needs_the_token_and_serves_pages_images_and_search(ui):
    assert req(ui, "/help/", auth=False)[0] == 401
    code, body = req(ui, "/help/manual/11-which-command")
    assert code == 200 and b"Which command do I use?" in body and b'href="/help/manual/04-nodes-and-shells"' in body
    assert b"http://" not in body.replace(b"http://127.0.0.1", b"")  # nothing third-party is loaded
    code, body = req(ui, "/help/search?q=governor")
    assert code == 200 and b"/help/manual/07-governor" in body
    assert req(ui, "/help/files/manual/img/web-overview.png")[0] == 200
    assert req(ui, "/help/manual/nope")[0] == 404 and req(ui, "/help/files/manual/README.md")[0] == 404
    assert req(ui, "/help/..%2F..%2FSPEC")[0] == 404 and req(ui, "/help/files/..%2F..%2Fsim.yaml")[0] == 404


def test_dashboard_links_to_the_help(ui):
    assert b'href="/help/"' in req(ui, "/")[1]


def test_node_detail_endpoint_returns_the_describe_text(ui):
    code, body = req(ui, "/api/nodes/gpu001")
    data = json.loads(body)
    assert code == 200 and data["info"]["name"] == "gpu001" and "Name:        gpu001" in data["text"]
    assert req(ui, "/api/nodes/zzz")[0] == 404
    assert req(ui, "/api/nodes/gpu001", auth=False)[0] == 401
    assert b"showNode" in req(ui, "/")[1] and b'id="nodedlg"' in req(ui, "/")[1]


def test_plan_endpoint_estimates_without_side_effects(ui):
    code, body = req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=4&gpus_per_node=8&parts=jira")
    data = json.loads(body)
    assert code == 200 and "2 cpu, 4 gpu" in data["text"] and data["total_mb"] > 700
    assert data["command"].startswith("benchctl up --cpu-nodes 2 --gpu-nodes 4") and "--without bmc" in data["command"]
    assert req(ui, "/api/plan?cpu_nodes=999")[0] == 422 and req(ui, "/api/plan?cpu_nodes=x")[0] == 422
    assert req(ui, "/api/plan", auth=False)[0] == 401
    assert b'id="plan"' in req(ui, "/")[1] and b"loadPlan" in req(ui, "/")[1]
