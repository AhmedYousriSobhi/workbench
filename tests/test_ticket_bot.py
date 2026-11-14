"""The ticket bot (issue 681): its decisions are deterministic for a seed, its calls go only to jira-mock, and the CLI switch is off by default."""
import importlib.util
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from typer.testing import CliRunner

from benchctl import cli, workload

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"images/login/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bot_mod, user_sim = _load("ticket_bot"), _load("user_sim")
runner = CliRunner()


def _t(key, status="Open", assignee=None):
    return {"key": key, "status": status, "assignee": assignee}


def test_full_flow_assign_start_comment_resolve_after_n_seconds():
    bot = bot_mod.Bot(random.Random(1), resolve_after=100, ignore=0, reopen=0)
    a = bot.step([_t("OPS-1")], 0)
    assert a[:2] == ("assign", "OPS-1") and a[2] in bot_mod.ENGINEERS
    assert bot.step([_t("OPS-1")], 1) == ("transition", "OPS-1", bot_mod.START)
    assert bot.step([_t("OPS-1", "In Progress")], 2)[:2] == ("comment", "OPS-1")
    assert bot.step([_t("OPS-1", "In Progress")], 50) is None           # not yet
    assert bot.step([_t("OPS-1", "In Progress")], 103) == ("transition", "OPS-1", bot_mod.RESOLVE)
    assert bot.step([_t("OPS-1", "Resolved")], 200) is None


def test_some_tickets_stay_unassigned_and_are_left_alone():
    bot = bot_mod.Bot(random.Random(1), ignore=1.0)
    assert bot.step([_t("OPS-1")], 0) is None and bot.stage["OPS-1"] == "ignored"
    assert bot.step([_t("OPS-1")], 500) is None                         # never picked up later


def test_a_resolved_ticket_can_be_reopened_and_worked_again():
    bot = bot_mod.Bot(random.Random(1), resolve_after=10, ignore=0, reopen=1.0)
    bot.step([_t("OPS-1")], 0)
    bot.step([_t("OPS-1")], 1)
    bot.step([_t("OPS-1", "In Progress")], 2)
    bot.step([_t("OPS-1", "In Progress")], 20)
    assert bot.step([_t("OPS-1", "Resolved")], 40) == ("transition", "OPS-1", bot_mod.REOPEN)
    assert bot.step([_t("OPS-1", "Open")], 41) == ("transition", "OPS-1", bot_mod.START)


def test_same_seed_same_actions_and_closed_tickets_are_dropped():
    def run(seed):
        bot = bot_mod.Bot(random.Random(seed))
        return [bot.step([_t(f"OPS-{n}") for n in range(1, 8)], n) for n in range(12)]
    assert run(3) == run(3) and any(run(3))
    bot = bot_mod.Bot(random.Random(1), ignore=0)
    bot.step([_t("OPS-1")], 0)
    bot.step([], 1)
    assert "OPS-1" not in bot.stage
    bot.step([_t("OPS-2", "Open")], 2)
    assert bot.step([_t("OPS-2", "Closed")], 3) is None


def test_tracked_tickets_are_bounded():
    bot = bot_mod.Bot(random.Random(1), ignore=0)
    tickets = [_t(f"OPS-{n}") for n in range(200)]
    for n in range(200):
        bot.step(tickets, n)
    assert sum(s != "done" for s in bot.stage.values()) <= bot_mod.MAX_TRACKED


def test_rhythm_matches_the_user_simulator():
    for h in (0, 3, 8, 14, 22):
        assert bot_mod.rhythm(h) == user_sim.rhythm(h)
    assert bot_mod.sim_hour(10, 60) == user_sim.sim_hour(10, 60)


def test_client_sends_the_calls_with_its_own_user_agent_and_survives_a_dead_mock():
    seen = []

    class H(BaseHTTPRequestHandler):
        def _go(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, self.rfile.read(n).decode(), self.headers["User-Agent"]))
            out = b'{"issues": [{"key": "OPS-9", "fields": {"status": {"name": "Open"}, "assignee": null}}]}'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(out)
        do_GET = do_POST = do_PUT = _go

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = bot_mod.Client(f"http://127.0.0.1:{srv.server_port}")
    assert c.tickets() == [{"key": "OPS-9", "status": "Open", "assignee": None}]
    c.do(("assign", "OPS-9", "bob"))
    c.do(("comment", "OPS-9", "hi"))
    c.do(("transition", "OPS-9", bot_mod.RESOLVE))
    assert [(m, p) for m, p, _, _ in seen] == [("POST", "/rest/api/2/search"), ("PUT", "/rest/api/2/issue/OPS-9/assignee"),
                                               ("POST", "/rest/api/2/issue/OPS-9/comment"), ("POST", "/rest/api/2/issue/OPS-9/transitions")]
    assert {ua for *_, ua in seen} == {bot_mod.UA} and '"Done"' in seen[-1][2]
    srv.shutdown()
    assert c.tickets() == [] and c.call("GET", "/x") is None


def test_the_bot_only_ever_targets_jira_mock():
    assert bot_mod.JIRA == "http://jira-mock:8080" and "jira-mock" in (ROOT / "images/login/ticket_bot.py").read_text()


def test_tickets_on_copies_the_script_and_validates():
    cmds = workload.tickets_on(60, 300, day=120, seed=4)
    assert cmds[0][:2] == ["docker", "cp"] and cmds[1] == workload.tickets_off() and cmds[2][:3] == ["docker", "exec", "-d"]
    assert cmds[2][-4:] == ["--day", "120", "--seed", "4"] and "--every" in cmds[2]
    for kw in ({"every": 5}, {"every": 4000}, {"resolve_after": 1}, {"day": 5}):
        try:
            workload.tickets_on(**kw)
        except workload.WorkloadError:
            continue
        raise AssertionError(kw)


def test_load_on_starts_the_bot_only_with_tickets(monkeypatch):
    ran = []
    monkeypatch.setattr(cli.stack, "_run", lambda cmd: ran.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    assert runner.invoke(cli.app, ["load", "on"]).exit_code == 0 and not any(workload.TICKET_PATH in " ".join(c) for c in ran)
    ran.clear()
    r = runner.invoke(cli.app, ["load", "on", "--tickets", "--ticket-every", "30"])
    assert r.exit_code == 0 and "ticket bot" in r.output and any(workload.TICKET_PATH in " ".join(c) for c in ran)
    assert runner.invoke(cli.app, ["load", "on", "--tickets", "--ticket-every", "1"]).exit_code == 1
