import json
import re
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

pytest.importorskip("starlette")
pytest.importorskip("uvicorn")
import uvicorn
from webui_src import page_source

from governor import control
from webui import __main__ as launcher
from webui.app import create_app, governor_status

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "test-token-123"
STATUS = {"ok": True, "level": 2, "dry_run": False, "mem_available": 3 * 2**30, "psi_some": 4.5, "psi_full": 0.1,
          "groups": ["compute"], "services": ["jira-mock"], "violations": [], "audit": ["L2: throttle p4"]}


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
        "  - {at: 0s, target: dcgm, do: {stop: true}}\n")
    (scen / "slow.yaml").write_text("name: slow\ndescription: d\nsteps:\n  - {at: 0s, target: dcgm, do: {stop: true}}\n"
                                    "  - {at: 1h, target: dcgm, do: {start: true}}\n")
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


def test_scenario_list_and_run_sends_slurm_steps_through_the_governor(ui):
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
    # the Slurm drain goes through the governor (the page never runs Docker itself); the dcgm step needs the host CLI
    assert state["ok"] is True and [s["status"] for s in state["steps"]] == ["ok", "skipped"]
    assert "host CLI" in state["steps"][1]["detail"]


def test_stop_ends_a_running_scenario_and_unknown_is_404(ui):
    h = {"X-Requested-With": "workbench"}
    assert req(ui, "/api/scenarios/nope/run", "POST", headers=h)[0] == 404
    code, body = req(ui, "/api/scenarios/quick")  # the preview a page shows before Play (issue 509)
    assert code == 200 and json.loads(body)["name"] == "quick" and "steps" in json.loads(body) and "cli" in json.loads(body)
    assert req(ui, "/api/scenarios/nope")[0] == 404
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
    assert out.strip() == "workbench UI: http://127.0.0.1:9123/login"  # piped (a log file): no token in it
    assert token not in out
    monkeypatch.setattr(launcher.sys.stdout, "isatty", lambda: True, raising=False)
    launcher.main()
    assert capsys.readouterr().out.strip() == f"workbench UI: http://127.0.0.1:9123/login?token={token}"  # a terminal gets the link
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


_SERVED: dict[str, bytes] = {}


def served_page(base: str) -> bytes:
    """What a browser gets: the page, its stylesheet and every module in the import graph from main.js, fetched over
    HTTP (so a file the page needs but the server does not serve fails here), as one blob."""
    if base not in _SERVED:
        parts = [req(base, "/")[1], req(base, "/static/app.css")[1], req(base, "/static/canvas.css")[1]]
        seen, todo = set(), ["main.js"]
        while todo:
            name = todo.pop()
            if name not in seen:
                seen.add(name)
                parts.append(req(base, f"/static/js/{name}")[1])
                todo += re.findall(r"from '\./([\w-]+\.js)'|import\('\./([\w-]+\.js)'\)", parts[-1].decode()) and \
                    [a or b for a, b in re.findall(r"from '\./([\w-]+\.js)'|import\('\./([\w-]+\.js)'\)", parts[-1].decode())]
        _SERVED[base] = b"\n".join(parts)
    return _SERVED[base]


def test_page_has_the_app_shell(ui):
    body = served_page(ui).decode()
    for needle in ('class="skip"', '<main id="main"', 'id="govdlg"', 'id="tab-run"',
                   'id="theme"', 'id="strip"', 'id="attn"', 'id="list"', 'id="flow"', 'id="viewseg"', 'id="crumb"', 'id="ovprev"'):
        assert needle in body, needle
    assert 'rel="stylesheet" href="/static/app.css"' in body and 'rel="stylesheet" href="/static/canvas.css"' in body and 'type="module" src="/static/js/main.js"' in body
    assert "prefers-reduced-motion" in req(ui, "/static/app.css")[1].decode()  # the stylesheet is served too


def test_static_files_need_the_token_and_have_the_right_type(ui):
    """ADR 0007: the stylesheet and the ES modules are static files behind the same token as the page."""
    font = "/static/fonts/inter-latin-wght-normal.woff2"
    for path in ("/static/app.css", "/static/canvas.css", "/static/js/main.js", "/static/js/state.js", font):
        assert req(ui, path, auth=False)[0] == 401, path
    assert req(ui, "/static/fonts/LICENSE-Inter.txt")[0] == 404  # only the font files are served, not their licence texts
    for path, kind in (("/static/app.css", "text/css"), ("/static/canvas.css", "text/css"), ("/static/js/main.js", "text/javascript"), (font, "font/woff2")):
        r = urllib.request.Request(ui + path, headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            assert resp.headers["Content-Type"].split(";")[0] == kind and resp.headers["Cache-Control"] == "no-cache", path


def test_page_and_static_files_carry_a_strict_csp_and_the_page_has_no_inline_style(ui):
    """#517: the policy blocks inline script and style, so none may remain in the markup or in the modules' templates."""
    for path in ("/", "/static/app.css", "/static/js/main.js"):
        r = urllib.request.Request(ui + path, headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            csp = resp.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp, path
    src = page_source()
    assert not re.search(r"\sstyle=\"", src) and not re.search(r"<style\b", (ROOT / "webui/static/index.html").read_text())
    assert not re.search(r"\son[a-z]+=\"", (ROOT / "webui/static/index.html").read_text())
    assert not re.search(r"setAttribute\('style'", src)


def test_every_module_the_page_imports_is_served_and_nothing_else_is(ui):
    """Walk the import graph from main.js over HTTP: every file answers, so no module is missing from static/js."""
    seen, todo = set(), ["main.js"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        code, body = req(ui, f"/static/js/{name}")
        assert code == 200, name
        todo += re.findall(r"from '\./([\w-]+\.js)'", body.decode()) + re.findall(r"import\('\./([\w-]+\.js)'\)", body.decode())
    assert {"state.js", "util.js", "shell.js"} <= seen
    for path in ("/static/../app.py", "/static/js/../../app.py", "/static/%2e%2e/app.py", "/static/index.html", "/static/js/nope.js", "/static/js"):
        assert req(ui, path)[0] == 404, path


def test_page_recovery_hint_needs_no_sudo():
    """ADR 0010: a dead governor is started with `benchctl start`; the sudo service is only an optional upgrade."""
    page = page_source()
    offline = next(line for line in page.splitlines() if "Governor not reachable" in line)
    assert "./scripts/benchctl start" in offline and offline.index("benchctl start") < offline.index("sudo")


def test_help_wiki_needs_the_token_and_serves_pages_images_and_search(ui):
    assert req(ui, "/help/", auth=False)[0] == 401
    code, body = req(ui, "/help/manual/11-which-command")
    assert code == 200 and b"Which command do I use?" in body and b'href="/help/manual/04-nodes-and-shells"' in body
    assert b"http://" not in body.replace(b"http://127.0.0.1", b"")  # nothing third-party is loaded
    code, body = req(ui, "/help/search?q=governor")
    assert code == 200 and b"/help/manual/07-governor" in body
    assert req(ui, "/help/files/manual/img/web-infrastructure.png")[0] == 200
    assert req(ui, "/help/manual/nope")[0] == 404 and req(ui, "/help/files/manual/README.md")[0] == 404
    assert req(ui, "/help/..%2F..%2FSPEC")[0] == 404 and req(ui, "/help/files/..%2F..%2Fsim.yaml")[0] == 404


def test_dashboard_links_to_the_help(ui):
    assert b'href="/help/"' in served_page(ui)


def test_node_detail_endpoint_returns_the_describe_text(ui):
    code, body = req(ui, "/api/nodes/gpu001")
    data = json.loads(body)
    assert code == 200 and data["info"]["name"] == "gpu001" and "Name:        gpu001" in data["text"]
    assert req(ui, "/api/nodes/zzz")[0] == 404
    assert req(ui, "/api/nodes/gpu001", auth=False)[0] == 401
    assert b"showNode" in served_page(ui) and b'id="nodepane"' in served_page(ui)


def test_plan_endpoint_estimates_without_side_effects(ui):
    code, body = req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=4&gpus_per_node=8&parts=jira")
    data = json.loads(body)
    assert code == 200 and "2 cpu, 4 gpu" in data["text"] and data["total_mb"] > 700
    assert data["command"].startswith("benchctl up --cpu-nodes 2 --gpu-nodes 4") and "--without bmc" in data["command"]
    assert data["command"].endswith("--app-mb 0") and "app under test" not in data["text"]  # the app already runs: no reserve, so Apply can show
    assert req(ui, "/api/plan?cpu_nodes=999")[0] == 422 and req(ui, "/api/plan?cpu_nodes=x")[0] == 422
    assert req(ui, "/api/plan", auth=False)[0] == 401
    assert b'id="plandlg"' in served_page(ui) and b"loadPlan" in served_page(ui)


def test_plan_takes_running_services_from_the_governor_not_from_docker(ui, monkeypatch):
    """The page has no Docker (W-3): what runs now comes from the governor's status (#339)."""
    from benchctl import workbench

    def boom(*a, **k):
        raise AssertionError("the web page must not run docker")

    monkeypatch.setattr(workbench, "running_services", boom)
    monkeypatch.setattr("subprocess.run", boom)
    code, body = req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=jira")
    assert code == 200 and json.loads(body)["running_mb"] > 0  # jira-mock is in the governor's list


def test_a_slow_plan_does_not_stall_the_other_requests(ui, monkeypatch):
    """/api/plan used to run its blocking work on the event loop and froze every feed meanwhile (#340)."""
    monkeypatch.setattr("webui.app.checks.mem_available", lambda: (time.sleep(1.5), 8 * 2**30)[1])
    slow = threading.Thread(target=req, args=(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8"))
    slow.start()
    time.sleep(0.3)
    t0 = time.time()
    assert req(ui, "/api/status")[0] == 200
    quick = time.time() - t0
    slow.join()
    assert quick < 0.8


def test_connections_endpoint_lists_every_entity_with_credential_paths_only(ui):
    code, body = req(ui, "/api/connections")
    rows = {e["id"]: e for e in json.loads(body)["entities"]}
    assert code == 200 and {"login", "nodes", "bmc", "bcm", "jira", "slurm", "gpu", "prometheus", "grafana", "ui"} <= set(rows)
    assert rows["login"]["address"] == "10.141.255.254:22" and "127.0.0.1:2222" in rows["login"]["how"] and rows["login"]["credential"].endswith("ssh/id_ed25519")
    assert rows["bmc"]["credential"].endswith("bmc_credentials") and "admin:<password>" in rows["bmc"]["command"]
    assert req(ui, "/api/connections", auth=False)[0] == 401
    page = served_page(ui)
    assert b'role="tablist"' in page and b'id="tab-conn"' in page and b"paintConnections" in page
    assert b'id="tab-traffic"' in page and b"paintTraffic" in page and b'aria-label="Workspaces"' in page


def test_plan_apply_needs_csrf_validates_and_asks_the_governor(ui):
    q = "/api/plan/apply?cpu_nodes=2&gpu_nodes=3&gpus_per_node=4&parts=jira"
    assert req(ui, q, "POST")[0] == 403  # CSRF header missing
    assert req(ui, "/api/plan/apply?cpu_nodes=999", "POST", headers={"X-Requested-With": "workbench"})[0] == 422
    code, body = req(ui, q, "POST", headers={"X-Requested-With": "workbench"})
    assert code == 200 and json.loads(body)["started"] is True
    assert b"pl-apply" in served_page(ui)


def test_jira_refresh_button_needs_csrf_and_asks_the_governor(ui):
    assert req(ui, "/api/jira-real/refresh", "POST")[0] == 403
    code, body = req(ui, "/api/jira-real/refresh", "POST", headers={"X-Requested-With": "workbench"})
    assert code == 200 and json.loads(body)["started"] is True
    assert b"data-post" in served_page(ui) and req(ui, "/api/jira-real/refresh", "POST", auth=False)[0] == 401


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
    assert b'id="expdlg"' in served_page(ui) and b"newer than its backend" in served_page(ui) and b"ssh-key install in a terminal" in served_page(ui) and b"Jira needs no token" in served_page(ui)


def test_power_off_asks_the_governor_to_freeze_the_node_so_slurm_notices_by_itself(tmp_path):
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
        assert asked == [{"cmd": "node-state", "node": "cn002", "state": "off"}]
        assert res[-1]["target"] == "slurm" and res[-1]["status"] == "ok"
        answer.update(ok=False, error="refused at L3")
        res = json.loads(req(base, "/api/nodes/cn002/power-on", "POST", headers=h)[1])["results"]
        assert asked[-1]["state"] == "on" and res[-1]["status"] == "skipped" and "refused at L3" in res[-1]["detail"]
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
    page = served_page(ui)
    assert b"/api/settings" in page and b'id="pl-parts"' in page and b"Cluster architecture" in page


def test_traffic_diagram_shapes_are_buttons_that_open_the_node_or_the_connection(ui):
    page = served_page(ui).decode()
    for needle in ("function asButton(", "role', 'button'", "openPop(", "showNode(id)", "openConnection(SVC_CONN[id])", "tr[data-id=",
                   "#flow #dots { pointer-events:none; }", "Enter' || e.key === ' '"):
        assert needle in page, needle


def test_page_script_declares_every_function_once():
    """A second `function name` silently replaces the first (hoisting): that emptied the traffic diagram (issue 142)."""
    import re
    page = page_source()
    names = re.findall(r"^(?:export )?(?:async )?function ([A-Za-z_$][\w$]*)", page, re.MULTILINE)  # a name in two modules would both be exposed by the test hook
    names = [n for n in names if not re.fullmatch(r"init\d*", n)]  # each module's own wiring entry point
    assert len(names) > 20 and [n for n in set(names) if names.count(n) > 1] == []


def test_settings_lists_the_nodes_for_the_bmc_picker_and_the_sheet_sends_the_choice(ui, monkeypatch, tmp_path):
    from benchctl import compose, workbench
    monkeypatch.setattr(workbench, "SAVED", tmp_path / "none.yaml")
    monkeypatch.setattr("benchctl.export.stack.current_profile", lambda: "min")
    body = json.loads(req(ui, "/api/settings")[1])
    assert [n["name"] for n in body["nodes"]] == ["cn001", "cn002", "cn003", "cn004", "gpu001", "gpu002"]
    assert all(n["bmc"] for n in body["nodes"]) and body["bmc_node_mb"] == compose.BMC_FORWARDER_MB
    page = served_page(ui).decode()
    assert 'id="pl-bmcnodes"' in page and "bmc_nodes" in page and "showBmcNodes" in page


def test_plan_with_a_bmc_selection_costs_less_and_names_the_flag(ui):
    full = json.loads(req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=bmc")[1])
    one = json.loads(req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=bmc&bmc_nodes=cn001")[1])
    assert full["total_mb"] - one["total_mb"] == 2 * 32 and "--bmc-nodes cn001" in one["command"]
    assert req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=bmc&bmc_nodes=..%2Fx")[0] == 422


def test_a_workload_button_asks_the_governor_to_submit_a_job_on_that_node(tmp_path):
    asked, answer = [], {"ok": True, "started": True}

    def handle(cmd, req):
        if cmd == "submit-job":
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
        code, body = req(base, "/api/nodes/gpu001/workload?gpus=4&duration=600", "POST", headers=h)[:2]
        assert code == 200 and json.loads(body)["started"] is True
        assert asked == [{"cmd": "submit-job", "node": "gpu001", "gpus": 4, "duration": 600}]
        assert req(base, "/api/nodes/nope/workload", "POST", headers=h)[0] == 404
        assert req(base, "/api/nodes/cn001/workload?gpus=x", "POST", headers=h)[0] == 422
        answer.update(ok=False, error="refused at L3")
        assert req(base, "/api/nodes/cn001/workload", "POST", headers=h)[0] == 409
    finally:
        server.should_exit = True
        gov.shutdown()


def test_a_resilience_scenario_goes_through_the_governor_and_the_page_gets_a_trace(tmp_path):
    asked = []
    trace = {"jobs": ["7 RUNNING cn001"], "nodes": {"cn001": {"slurmd": "5", "slurmstepd": "9"}},
             "logs": {"workbench-slurmctld-1": ["slurmctld: Node cn001 not responding"]}}

    def handle(cmd, request):
        asked.append((cmd, request))
        return {"ok": True, "trace": trace} if cmd == "slurm-trace" else {"ok": True, "started": True} if cmd != "status" else STATUS

    sock = str(tmp_path / "g.sock")
    gov = control.serve(sock, handle, lambda: {"compute"})
    (tmp_path / "res.yaml").write_text(
        "name: res\ndescription: d\nsteps:\n  - {at: 0s, target: slurm, do: {submit: {node: cn001, duration: 60}}}\n"
        "  - {at: 0s, target: slurm, do: {slurmd_restart: [cn001]}}\n")
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, sock, tmp_path, ROOT / "sim.yaml", fetch=no_stack, poster=no_stack),
                                           host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    base, h = f"http://127.0.0.1:{port}", {"X-Requested-With": "workbench"}
    try:
        assert req(base, "/api/scenarios/res/run?trace=1", "POST", headers=h)[0] == 200
        state = {}
        deadline = time.time() + 10
        while time.time() < deadline:
            state = json.loads(req(base, "/api/scenarios/run")[1])
            if not state["running"]:
                break
            time.sleep(0.05)
        assert state["ok"] is True and [s["status"] for s in state["steps"]] == ["ok", "ok"]
        cmds = [(c, r.get("name") or r.get("node")) for c, r in asked if c in ("submit-job", "slurm-step")]
        assert cmds == [("slurm-step", "slurmd-restart"), ("submit-job", "cn001")] or cmds == [("submit-job", "cn001"), ("slurm-step", "slurmd-restart")]
        assert [e["label"][:5] for e in state["trace"]] == ["slurm", "slurm"]
        assert "Node cn001 not responding" in state["trace"][0]["logs"][0]["line"] and "slurmstepd=9" in state["trace"][0]["after"]
        # without the flag there is no trace; and with an unreachable governor it is refused up front
        assert req(base, "/api/scenarios/res/run", "POST", headers=h)[0] == 200
        time.sleep(1)
        assert json.loads(req(base, "/api/scenarios/run")[1])["trace"] is None
    finally:
        server.should_exit = True
        gov.shutdown()


def test_licence_watch_reads_and_renews_once_when_the_end_is_near(monkeypatch):
    """Detect (read the end over HTTP) and renew through the governor, once per cooldown, only when under the threshold (issue 293)."""
    import asyncio
    import time

    from benchctl import checks, jira_real
    from webui import licence

    asked = []
    monkeypatch.setattr(checks, "governor_request", lambda req, sock: asked.append(req) or {"ok": True, "started": True})
    st = {"valid": True, "expires": int(time.time()) + 3 * 3600}
    monkeypatch.setattr(jira_real, "licence_status", lambda *a, **k: st)
    state = {}
    asyncio.run(licence.watch(state, "sock", every=0, rounds=2))
    assert asked == [] and "valid until" in state["text"]  # 3 h left: only detected
    st = {"valid": True, "expires": int(time.time()) + 300}  # 5 min left
    asyncio.run(licence.watch(state, "sock", every=0, rounds=3))
    assert asked == [{"cmd": "refresh-jira-licence"}] and "renewing" in state["text"]  # asked once in three rounds
    monkeypatch.setenv(jira_real.AUTO_ENV, "0")
    state["asked"] = 0.0
    asyncio.run(licence.watch(state, "sock", every=0, rounds=1))
    assert len(asked) == 1  # automatic renewal off
    monkeypatch.setattr(jira_real, "licence_status", lambda *a, **k: None)
    asyncio.run(licence.watch(state, "sock", every=0, rounds=1))
    assert state["text"] == "" and len(asked) == 1  # Jira down: quiet


def test_plan_reports_the_admitted_number_next_to_the_caps(ui):
    data = json.loads(req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=4&gpus_per_node=8&parts=jira")[1])
    assert data["need_mb"] == data["expected_mb"] - data["running_mb"] < data["total_mb"]  # no app reserve on the page; expected is below the caps
    assert b"p.need_mb" in served_page(ui)


def test_diagram_has_a_colour_legend_and_combinable_overlays(ui):
    """Kind is colour (simulated vs real), state is the border, traffic and fault are overlays that combine (issue 297)."""
    page = served_page(ui)
    for needle in (b'id="ckey"', b"simulated (dashed stripe)", b"real (solid stripe)", b"a fault applied in the last 30 s",
                   b"They combine", b"const REAL_SERVICES", b"'ff-' + k", b"'fx-' + k", b"'fa-' + k"):
        assert needle in page, needle
    assert b"'Jira mock'" not in page and b"'BCM mock'" not in page  # the word is not in the boxes: colour says it


def test_state_update_keeps_the_kind_class_of_a_service_box(ui):
    assert b"(k === 'teleport' ? '' : REAL_SERVICES.has(k) ? ' real' : ' sim')" in served_page(ui)  # repainting the state must not drop the colour of the kind


def test_scenarios_live_in_the_test_run_workspace_not_a_drawer():
    """Issue 509: scenario, run, expectations and Client's eye share one workspace; the drawer is gone."""
    html = page_source()
    for needle in ('id="tab-run"', 'id="tb-run"', 'id="scn-toggle"', 'id="run-eye"', 'id="runcli"', 'id="run-md"', 'id="run-json"', "li.classList.toggle('active'", "function scnOpen("):
        assert needle in html, needle
    assert "scndlg" not in html and "dialog.drawer" not in html



def test_traffic_has_filters_inspector_and_path_following():
    html = page_source()
    for needle in ('id="tr-sw"', 'id="tr-ev"', 'id="tr-insp"', 'id="tr-path"', "function pathOf", "The web page server is older than this page", "/api/monitoring", "rateOf(", 'id="tr-map"', 'id="tr-reset"', 'id="tr-chips"', 'id="tr-zin"', 'id="tr-feed"', 'id="tr-chart"', "paintTrTimeline", 'id="kpis"', 'id="subseg"', "paintKpis", "dbdLink", 'data-part', "PART_INFO", "data-mv", "paintTrPart", "workbench-tr-order1", 'id="tr-move"', "paintTrafficMap", '<html lang="en" data-theme="midnight">'):
        assert needle in html, needle


def test_slurm_stats_come_from_the_governor(tmp_path):
    stats = {"rpcs": 5, "threads": 2, "top": []}
    sock = str(tmp_path / "g.sock")
    gov = control.serve(sock, lambda cmd, request: {"ok": True, "stats": stats} if cmd == "slurm-stats" else STATUS, lambda: {"compute"})
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, sock, tmp_path, ROOT / "sim.yaml", fetch=no_stack, poster=no_stack),
                                           host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    try:
        assert json.loads(req(f"http://127.0.0.1:{port}", "/api/slurm-stats")[1]) == {"stats": stats}
    finally:
        server.should_exit = True
        gov.shutdown()


def test_the_inline_script_has_valid_javascript_syntax(tmp_path):
    """A stray quote in one string once broke the whole page while every string-matching test still passed."""
    import re
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = page_source()
    js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.DOTALL))
    f = tmp_path / "page.js"
    f.write_text(js)
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr[:400]


def test_traffic_view_follows_node_state_and_logs_changes(ui):
    page = served_page(ui).decode()
    # issue 414: node health reaches the parts, the link state text, the part inspector and the timeline
    for marker in ("function nodeHealth", "noteNodeChanges", "slurmNodes = s && s.node_states", "renderCluster(JSON.parse(text)); paintTraffic()",
                   "from: 'state'", "nodes out"):
        assert marker in page, marker


def test_plan_names_the_bcm_kubernetes_flag_and_the_sheet_has_the_switch(ui, monkeypatch, tmp_path):
    from benchctl import workbench
    monkeypatch.setattr(workbench, "SAVED", tmp_path / "none.yaml")
    off = json.loads(req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=bcm,k8s&bcm_k8s=0")[1])
    on = json.loads(req(ui, "/api/plan?cpu_nodes=2&gpu_nodes=1&gpus_per_node=8&parts=bcm,k8s&bcm_k8s=1")[1])
    assert "--no-bcm-k8s" in off["command"] and "--bcm-k8s" in on["command"] and "--no-bcm-k8s" not in on["command"]
    assert json.loads(req(ui, "/api/settings")[1])["shapes"]["bcm"] == {"manage_kubernetes": True}
    assert 'id="pl-bcmk8s"' in served_page(ui).decode()


def test_shared_status_makes_one_governor_call_per_ttl_however_many_streams_ask():
    """#451: every open stream used to call the governor socket each second."""
    import asyncio

    from webui.app import SharedStatus
    now, calls = [0.0], []

    def fetch():
        calls.append(1)
        return {"level": len(calls)}

    async def run():
        s = SharedStatus(fetch, 1.0, clock=lambda: now[0])
        got = await asyncio.gather(*[s.get() for _ in range(50)])  # fifty streams at once: one call
        assert len(calls) == 1 and all(g == {"level": 1} for g in got)
        now[0] = 0.5
        await asyncio.gather(*[s.get() for _ in range(50)])
        assert len(calls) == 1  # still fresh
        now[0] = 1.2
        got = await asyncio.gather(*[s.get() for _ in range(50)])
        assert len(calls) == 2 and all(g == {"level": 2} for g in got)
    asyncio.run(run())


def test_every_static_folder_is_packaged():
    """A non-editable install ships only the files `package-data` names: a new folder under static/ must be listed."""
    import tomllib
    listed = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["webui"]
    folders = {p.name for p in (ROOT / "webui" / "static").iterdir() if p.is_dir()}
    assert {"js"} <= folders and [f for f in folders if f"static/{f}/*" not in listed] == []


def test_the_traffic_record_needs_the_token_and_a_numeric_since(ui):
    """issue 501: /api/traffic merges the mocks' own record (whatever a stack on this host answers, or nothing)."""
    assert req(ui, "/api/traffic", auth=False)[0] == 401
    assert req(ui, "/api/traffic?since=abc")[0] == 400
    code, body = req(ui, "/api/traffic?since=1.5")
    got = json.loads(body)
    assert code == 200 and isinstance(got["streams"], list) and isinstance(got["recent"], list)
    assert got["services"].keys() == {"bmc-farm", "bcm-mock", "jira-mock"}


def test_the_capture_routes_go_only_through_the_governor_and_check_their_input(tmp_path):
    """issue 505: start, read and stop are governor commands; the page validates the node name and needs the CSRF header to start or stop."""
    seen = []

    def handle(cmd, request):
        seen.append((cmd, request))
        return {"ok": True, "running": True, "messages": [], "next": 0} if cmd.startswith("capture") else STATUS
    sock = str(tmp_path / "g.sock")
    gov = control.serve(sock, handle, lambda: {"compute"})
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, sock, tmp_path, ROOT / "sim.yaml", fetch=no_stack, poster=no_stack), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    base = f"http://127.0.0.1:{port}"

    def post(path, body):
        r = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"Authorization": f"Bearer {TOKEN}", "X-Requested-With": "workbench", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status
        except HTTPError as e:
            return e.code
    try:
        assert post("/api/capture/start", {"node": "cn001"}) == 200 and seen[-1] == ("capture-start", {"cmd": "capture-start", "node": "cn001"})
        assert post("/api/capture/start", {"node": "cn001; rm -rf /"}) == 400 and post("/api/capture/start", {}) == 400 and post("/api/capture/start", {"node": 3}) == 400
        assert req(base, "/api/capture/read?since=7")[0] == 200 and seen[-1][1] == {"cmd": "capture-read", "since": 7}
        assert req(base, "/api/capture/read?since=abc")[0] == 400
        assert req(base, "/api/capture/read?since=-5")[0] == 200 and seen[-1][1]["since"] == 0
        assert post("/api/capture/stop", {}) == 200 and seen[-1][0] == "capture-stop"
        assert req(base, "/api/capture/start", method="POST")[0] in (400, 403)  # no CSRF header: refused before the governor is asked
        n = len(seen)
        assert req(base, "/api/capture/read", auth=False)[0] == 401 and len(seen) == n
    finally:
        server.should_exit = True
        gov.shutdown()


def test_html_text_sizes_are_rem_and_only_the_svg_diagrams_keep_px():
    """Review 11.4 (issue 521): the browser's font-size setting is honoured by every HTML rule; a diagram's text is in px because
    its geometry is fixed in user units and would otherwise overflow its boxes."""
    svg_rule = re.compile(r"#flow|svg|\btext\b|tspan|\.an-(nt|ns|tubelab|tubedir)\b|#tr-map")
    bad = []
    for css in sorted((ROOT / "webui/static").glob("*.css")):
        for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css.read_text()):
            if not svg_rule.search(sel) and re.search(r"font(?:-size)?:[^;]*?\d(?:\.\d+)?px", body):
                bad.append(f"{css.name}: {sel.strip()[:60]}")
    assert not bad, bad
    assert re.search(r"\.small\s*\{\s*font-size:\s*0\.875rem", (ROOT / "webui/static/app.css").read_text())  # 14 px floor for running text
