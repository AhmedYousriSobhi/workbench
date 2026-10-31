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


def no_stack(*args, **kw):
    raise OSError("no stack in unit tests")  # a stack running on the host must not change the result


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
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, sock, scen, ROOT / "sim.yaml", interval=0.05, fetch=no_stack, poster=no_stack),
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
    for needle in ('class="skip"', '<main id="main"', 'id="govdlg"', 'id="scndlg"',
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
    assert b"showNode" in req(ui, "/")[1] and b'id="nodepane"' in req(ui, "/")[1]


def test_plan_endpoint_estimates_without_side_effects(ui):
    code, body = req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=4&gpus_per_node=8&parts=jira")
    data = json.loads(body)
    assert code == 200 and "2 cpu, 4 gpu" in data["text"] and data["total_mb"] > 700
    assert data["command"].startswith("benchctl up --cpu-nodes 2 --gpu-nodes 4") and "--without bmc" in data["command"]
    assert req(ui, "/api/plan?cpu_nodes=999")[0] == 422 and req(ui, "/api/plan?cpu_nodes=x")[0] == 422
    assert req(ui, "/api/plan", auth=False)[0] == 401
    assert b'id="plandlg"' in req(ui, "/")[1] and b"loadPlan" in req(ui, "/")[1]


def test_connections_endpoint_lists_every_entity_with_credential_paths_only(ui):
    code, body = req(ui, "/api/connections")
    rows = {e["id"]: e for e in json.loads(body)["entities"]}
    assert code == 200 and {"login", "nodes", "bmc", "bcm", "jira", "slurm", "gpu", "prometheus", "grafana", "ui"} <= set(rows)
    assert rows["login"]["address"] == "10.141.255.254:22" and "127.0.0.1:2222" in rows["login"]["how"] and rows["login"]["credential"].endswith("ssh/id_ed25519")
    assert rows["bmc"]["credential"].endswith("bmc_credentials") and "admin:<password>" in rows["bmc"]["command"]
    assert req(ui, "/api/connections", auth=False)[0] == 401
    page = req(ui, "/")[1]
    assert b'role="tablist"' in page and b'id="tab-conn"' in page and b"paintConnections" in page


def test_plan_apply_needs_csrf_validates_and_asks_the_governor(ui):
    q = "/api/plan/apply?cpu_nodes=2&gpu_nodes=3&gpus_per_node=4&parts=jira"
    assert req(ui, q, "POST")[0] == 403  # CSRF header missing
    assert req(ui, "/api/plan/apply?cpu_nodes=999", "POST", headers={"X-Requested-With": "workbench"})[0] == 422
    code, body = req(ui, q, "POST", headers={"X-Requested-With": "workbench"})
    assert code == 200 and json.loads(body)["started"] is True
    assert b"pl-apply" in req(ui, "/")[1]


def test_export_button_downloads_the_profile_and_explains_a_missing_format(ui, monkeypatch, tmp_path):
    from benchctl import workbench
    monkeypatch.setattr(workbench, "SAVED", tmp_path / "none.yaml")
    monkeypatch.setattr("benchctl.export.stack.current_profile", lambda: "min")  # not whatever the host last started
    monkeypatch.setattr("benchctl.export.LOCAL_FORMAT_FILE", tmp_path / "none")
    monkeypatch.setattr("benchctl.export.format_id.__defaults__", (None, None, tmp_path / "none"))
    monkeypatch.delenv("BENCH_EXPORT_FORMAT", raising=False)
    code, body = req(ui, "/api/export/profile")
    assert code == 422 and ".git/export-format" in json.loads(body)["detail"]
    monkeypatch.setenv("BENCH_EXPORT_FORMAT", "demo:2")
    code, body = req(ui, "/api/export/profile?bmc=1")
    data = json.loads(body)
    assert code == 200 and data["format"] == "demo" and data["version"] == 2 and len(data["bmc"]) == 6
    assert "token" not in body.decode().lower() and req(ui, "/api/export/profile", auth=False)[0] == 401
    assert b'id="expdlg"' in req(ui, "/")[1] and b"newer than its backend" in req(ui, "/")[1] and b"ssh-key install in a terminal" in req(ui, "/")[1] and b"Jira needs no token" in req(ui, "/")[1]


def test_power_off_also_asks_the_governor_to_mark_the_node_down_in_slurm(tmp_path):
    """The BMC and BCM change, but sinfo only follows if Slurm is told; the page has no Docker, so the governor does it."""
    asked = []
    answer = {"ok": True, "started": True}

    def handle(cmd, req):
        if cmd == "node-state":
            asked.append(req)
            return answer
        return STATUS

    sock = str(tmp_path / "g.sock")
    gov = control.serve(sock, handle, lambda: {"compute"})
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, sock, tmp_path, ROOT / "sim.yaml", fetch=no_stack, poster=lambda u, b: {}),
                                           host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    base, h = f"http://127.0.0.1:{port}", {"X-Requested-With": "workbench"}
    try:
        res = json.loads(req(base, "/api/nodes/cn002/power-off", "POST", headers=h)[1])["results"]
        assert asked == [{"cmd": "node-state", "node": "cn002", "state": "down"}]
        assert res[-1]["target"] == "slurm" and res[-1]["status"] == "ok"
        answer.update(ok=False, error="refused at L3")
        res = json.loads(req(base, "/api/nodes/cn002/power-on", "POST", headers=h)[1])["results"]
        assert asked[-1]["state"] == "resume" and res[-1]["status"] == "skipped" and "refused at L3" in res[-1]["detail"]
        res = json.loads(req(base, "/api/nodes/cn002/overheat", "POST", headers=h)[1])["results"]
        assert not any(r["target"] == "slurm" for r in res)
    finally:
        server.should_exit = True
        gov.shutdown()


def test_settings_endpoint_serves_the_option_registry_and_the_sheet_draws_from_it(ui, monkeypatch, tmp_path):
    from benchctl import workbench
    monkeypatch.setattr(workbench, "SAVED", tmp_path / "none.yaml")
    code, body = req(ui, "/api/settings")
    parts = {p["id"]: p for p in json.loads(body)["parts"]}
    assert code == 200 and set(parts) == set(workbench.PARTS) and parts["accounting"]["mb"] > 0 and parts["accounting"]["label"]
    assert req(ui, "/api/settings", auth=False)[0] == 401
    page = req(ui, "/")[1]
    assert b"/api/settings" in page and b'id="pl-parts"' in page and b"\xe2\x9a\x99 Settings" in page


def test_traffic_diagram_shapes_are_buttons_that_open_the_node_or_the_connection(ui):
    page = req(ui, "/")[1].decode()
    for needle in ("function asButton(", "role', 'button'", "showNode(n.name)", "openConnection(SVC_CONN[k])", "tr[data-id=",
                   "#flow #dots { pointer-events:none; }", "Enter' || e.key === ' '"):
        assert needle in page, needle


def test_page_script_declares_every_function_once():
    """A second `function name` silently replaces the first (hoisting): that emptied the traffic diagram (issue 142)."""
    import re
    page = (ROOT / "webui" / "static" / "index.html").read_text()
    names = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)", page, re.MULTILINE)
    assert len(names) > 20 and [n for n in set(names) if names.count(n) > 1] == []


def test_settings_lists_the_nodes_for_the_bmc_picker_and_the_sheet_sends_the_choice(ui, monkeypatch, tmp_path):
    from benchctl import compose, workbench
    monkeypatch.setattr(workbench, "SAVED", tmp_path / "none.yaml")
    monkeypatch.setattr("benchctl.export.stack.current_profile", lambda: "min")
    body = json.loads(req(ui, "/api/settings")[1])
    assert [n["name"] for n in body["nodes"]] == ["cn001", "cn002", "cn003", "cn004", "gpu001", "gpu002"]
    assert all(n["bmc"] for n in body["nodes"]) and body["bmc_node_mb"] == compose.BMC_FORWARDER_MB
    page = req(ui, "/")[1].decode()
    assert 'id="pl-bmcnodes"' in page and "bmc_nodes" in page and "showBmcNodes" in page


def test_plan_with_a_bmc_selection_costs_less_and_names_the_flag(ui):
    full = json.loads(req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=bmc")[1])
    one = json.loads(req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=bmc&bmc_nodes=cn001")[1])
    assert full["total_mb"] - one["total_mb"] == 2 * 32 and "--bmc-nodes cn001" in one["command"]
    assert req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=bmc&bmc_nodes=..%2Fx")[0] == 422
