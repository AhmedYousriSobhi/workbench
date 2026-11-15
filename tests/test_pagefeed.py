"""A `benchctl scenario run` shown on the web page, best effort (ADR 0086 phase 4, issue 811): benchctl/pagefeed.py posts, the page's
Runs keeps it in memory. Hermetic: the pages here are this test's own (the real app on a free port, or sockets that never answer)."""
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest
import yaml

pytest.importorskip("starlette")
pytest.importorskip("uvicorn")
import uvicorn
from starlette.exceptions import HTTPException
from typer.testing import CliRunner

from benchctl import cli, pagefeed, scenario
from webui.app import CLI_QUIET_S, Runs, create_app

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "feed-test-token"
QUICK = ("name: quick\ndescription: d\nsteps:\n  - {at: 0s, target: bmc-farm, do: {nodes: [cn001], temp: 90}}\n"
         "  - {at: 1s, target: ssh, do: {state: down}}\nexpect:\n  - slurm.node_state: {node: cn001, state: drain, within: 5s}\n")
SLOW = "name: slow\ndescription: d\nsteps:\n  - {at: 0s, target: dcgm, do: {stop: true}}\n  - {at: 1h, target: dcgm, do: {start: true}}\n"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def no_stack(*args, **kw):
    raise OSError("no stack in unit tests")


@pytest.fixture
def scen(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir()
    (d / "quick.yaml").write_text(QUICK)
    (d / "slow.yaml").write_text(SLOW)
    return d


@pytest.fixture
def page(tmp_path, scen):
    """The real page app on a free port, no governor and no stack behind it."""
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, str(tmp_path / "no-gov.sock"), scen, ROOT / "sim.yaml", fetch=no_stack, poster=no_stack),
                                           host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def token_file(tmp_path):
    f = tmp_path / "ui_token"
    f.write_text(TOKEN + "\n")
    return f


def sc_of(scen, name="quick") -> dict:
    return yaml.safe_load((scen / f"{name}.yaml").read_text())


def get(base, path):
    r = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(r, timeout=5) as resp:
        return json.loads(resp.read())


def raw(base, body: bytes, headers=None):
    h = {"Authorization": f"Bearer {TOKEN}", "X-Requested-With": "workbench"}
    h.update(headers or {})
    r = urllib.request.Request(base + "/api/scenarios/cli", data=body, method="POST", headers={k: v for k, v in h.items() if v is not None})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def play(feed, sc):
    """What scenario.run tells the feed for `sc`: every step ok, every expectation held."""
    for s in scenario.plan(sc):
        feed.step(s, scenario.Outcome("x", "ok", "done on the host"))
    for exp in sc.get("expect") or []:
        feed.expect(scenario.Outcome(scenario.expectation(exp, host=False)[0], "ok"))


def test_a_cli_run_shows_on_the_page_with_its_real_results(page, scen, token_file):
    said, sc = [], sc_of(scen)
    feed = pagefeed.Feed(sc, 2.0, said.append, token_file=token_file, url=page + "/api/scenarios/cli")
    play(feed, sc)
    feed.close(True, False)
    st = get(page, "/api/scenarios/run")
    assert said == []
    assert (st["name"], st["source"], st["running"], st["ok"], st["speed"]) == ("quick", "cli", False, True, 2.0)
    assert [s["status"] for s in st["steps"]] == ["ok", "ok"] and st["steps"][1]["page"] is False  # the ssh step: CLI only on the page, ran here
    assert st["steps"][1]["ids"] == ["login"] and st["steps"][0]["detail"] == "done on the host"
    assert [(x["e"], x["status"]) for x in st["expects"]] == [(1, "ok")]


def test_with_no_page_listening_the_cli_run_is_exactly_as_before(monkeypatch, scen, token_file):
    """The acceptance test: a token file but nothing on the port. Same output, same exit code, and no wait."""
    monkeypatch.setenv("BENCHCTL_UI_PORT", str(free_port()))
    sc = sc_of(scen)
    monkeypatch.setattr(cli.scenario, "load", lambda name: sc)

    def fake_run(sc, say, on_step, on_expect, **kw):
        for s in scenario.plan(sc):
            o = scenario.Outcome(f"{s.target} step", "ok")
            on_step(s, o)
            say(f"[{s.at:6.1f}s] ok      {o.label}")
        return scenario.Report("quick", steps=[scenario.Outcome("s", "ok")])
    monkeypatch.setattr(cli.scenario, "run", fake_run)
    runner = CliRunner()
    before = runner.invoke(cli.app, ["scenario", "run", "quick"])  # the conftest guard: no token, the feed is off
    monkeypatch.setattr(pagefeed, "TOKEN", token_file)
    t = time.monotonic()
    after = runner.invoke(cli.app, ["scenario", "run", "quick"])
    assert time.monotonic() - t < 1.0
    assert (after.exit_code, after.output) == (before.exit_code, before.output) and "web page" not in after.output


def test_a_slow_page_never_holds_the_run_and_is_said_once(token_file):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)  # accepts, never answers
    said, sc = [], {"name": "quick", "steps": [{"at": "0s", "target": "bmc-farm", "do": {"nodes": ["cn001"]}}] * 3}
    try:
        t = time.monotonic()
        feed = pagefeed.Feed(sc, 1.0, said.append, token_file=token_file, url=f"http://127.0.0.1:{srv.getsockname()[1]}/api/scenarios/cli", timeout=0.3)
        play(feed, sc)
        assert time.monotonic() - t < 0.1  # the steps never wait for the page
        feed.close(True, False)
        assert time.monotonic() - t < pagefeed.FLUSH + 0.3
        feed.thread.join(1)
        assert said == ["web page: no answer within 0.3 s, so the rest of this run is not shown there"]
    finally:
        srv.close()


def test_a_page_busy_with_its_own_run_refuses_and_the_cli_says_so_once(page, scen, token_file):
    r = urllib.request.Request(page + "/api/scenarios/slow/run", method="POST", data=b"", headers={"Authorization": f"Bearer {TOKEN}", "X-Requested-With": "workbench"})
    urllib.request.urlopen(r, timeout=5).close()
    said, sc = [], sc_of(scen)
    feed = pagefeed.Feed(sc, 1.0, said.append, token_file=token_file, url=page + "/api/scenarios/cli")
    play(feed, sc)
    feed.close(True, False)
    assert said == ["web page: this run is not shown there: slow is already running on the page"]
    st = get(page, "/api/scenarios/run")
    assert (st["name"], st["source"], st["running"]) == ("slow", "page", True)


def test_another_token_is_refused_and_never_printed(page, scen, tmp_path):
    wrong = tmp_path / "wrong"
    wrong.write_text("not-the-page-token")
    said, sc = [], sc_of(scen)
    feed = pagefeed.Feed(sc, 1.0, said.append, token_file=wrong, url=page + "/api/scenarios/cli")
    feed.close(True, False)
    assert said == ["web page: this run is not shown there: it refused the page token (HTTP 401; is it another checkout's page?)"]
    assert "not-the-page-token" not in said[0] and get(page, "/api/scenarios/run")["running"] is False


def test_the_posts_carry_results_only(scen, token_file):
    """Nothing but the scenario's name, the plan's fingerprint and per item a status, a detail and a time; the token goes in the header."""
    bodies, sc = [], sc_of(scen)

    def post(url, token, body, timeout):
        assert url.startswith("http://127.0.0.1:") and token == TOKEN
        bodies.append(body)
        return {"run": "r1"} if body["event"] == "start" else {"ok": True}
    feed = pagefeed.Feed(sc, 1.0, print, token_file=token_file, post=post)
    play(feed, sc)
    feed.close(True, False)
    keys = {b["event"]: set(b) for b in bodies}
    assert keys == {"start": {"event", "name", "speed", "key"}, "step": {"event", "i", "status", "detail", "t", "run"},
                    "expect": {"event", "label", "status", "detail", "t", "run"}, "end": {"event", "ok", "stopped", "run"}}
    assert TOKEN not in json.dumps(bodies) and [b["i"] for b in bodies if b["event"] == "step"] == [0, 1]


def test_the_feed_beats_while_no_step_happens(scen, token_file):
    bodies = []

    def post(url, token, body, timeout):
        bodies.append(body["event"])
        return {"run": "r1"}
    feed = pagefeed.Feed(sc_of(scen), 1.0, print, token_file=token_file, post=post, beat=0.05)
    time.sleep(0.3)
    feed.close(True, False)
    assert bodies[0] == "start" and bodies.count("beat") >= 2 and bodies[-1] == "end"


def test_the_endpoint_checks_the_token_size_and_shape(page, scen):
    key = scenario.plan_key(scenario.plan(sc_of(scen)))
    start = json.dumps({"event": "start", "name": "quick", "speed": 1, "key": key}).encode()
    assert raw(page, start, {"Authorization": None})[0] == 401
    assert raw(page, start, {"X-Requested-With": None})[0] == 403
    assert raw(page, b"{" + b" " * 4096 + b"}")[0] == 413
    assert raw(page, b"not json")[0] == 422 and raw(page, b"[1]")[0] == 422
    assert raw(page, json.dumps({"event": "start", "name": "quick", "speed": 1, "key": "other"}).encode())[0] == 409  # another copy of the scenario
    assert raw(page, json.dumps({"event": "start", "name": "nope", "speed": 1, "key": key}).encode())[0] == 404
    assert raw(page, json.dumps({"event": "start", "name": "quick", "speed": "fast", "key": key}).encode())[0] == 422
    code, got = raw(page, start)
    assert code == 200
    run = got["run"]
    assert raw(page, json.dumps({"event": "step", "run": "guess", "i": 0, "status": "ok", "detail": "", "t": 1}).encode())[0] == 409
    for bad in ({"i": 9}, {"i": True}, {"status": "passed"}, {"t": -1}, {"detail": 5}):
        assert raw(page, json.dumps({"event": "step", "run": run, "i": 0, "status": "ok", "detail": "", "t": 1, **bad}).encode())[0] == 422, bad
    assert raw(page, json.dumps({"event": "expect", "run": run, "label": "made up", "status": "ok", "detail": "", "t": 1}).encode())[0] == 422
    assert raw(page, json.dumps({"event": "step", "run": run, "i": 0, "status": "ok", "detail": "x" * 1000, "t": 1}).encode())[0] == 200
    assert len(get(page, "/api/scenarios/run")["steps"][0]["detail"]) == 300
    assert raw(page, start)[0] == 409  # one run at a time: a second CLI run is refused too
    assert raw(page, b"")[0] == 422  # an empty body
    r = urllib.request.Request(page + "/api/scenarios/quick/run", method="POST", data=b"", headers={"Authorization": f"Bearer {TOKEN}", "X-Requested-With": "workbench"})
    with pytest.raises(HTTPError) as e:
        urllib.request.urlopen(r, timeout=5)
    assert e.value.code == 409 and "running from the CLI" in json.loads(e.value.read())["detail"]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_a_cli_run_that_stops_posting_is_dropped(scen):
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    clock, notes = Clock(), []

    class Flow:
        def note(self, target, label, nodes):
            notes.append((target, nodes))
    runs = Runs(scen, sim, Flow(), clock=clock)
    run = runs.cli_event({"event": "start", "name": "quick", "speed": 1, "key": scenario.plan_key(scenario.plan(sc_of(scen)))})["run"]
    runs.cli_event({"event": "step", "run": run, "i": 0, "status": "ok", "detail": "", "t": 0.1})
    assert notes == [("bmc-farm", ["cn001"])]  # the same traffic mark a page run makes
    assert runs.stop() == {"stopping": False, "why": "a run from the CLI stops with Ctrl+C in its terminal"}
    clock.now += CLI_QUIET_S - 1
    assert runs.snapshot()["running"] is True
    clock.now += 2
    st = runs.snapshot()
    assert (st["running"], st["stopped"], st["lost"], st["source"]) == (False, True, True, "cli")
    with pytest.raises(HTTPException) as e:
        runs.cli_event({"event": "beat", "run": run})
    assert e.value.status_code == 409
    assert runs.start("slow", 1.0) == {"started": "slow"}  # the page may play again
    runs.stop()
