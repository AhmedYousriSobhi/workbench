"""The agent verbs of issue 635 (benchctl/agentverbs.py): scenario status, clear faults, one-shot capture. Hermetic."""
import json

from typer.testing import CliRunner

from benchctl import agentverbs, cli, stack

runner = CliRunner()
SIM = {"compute": {"prefix": "cn", "count": 2}, "gpu": {"prefix": "gpu", "nodes": 1}}


def test_scenario_status_drops_do_and_counts_done_steps():
    got = agentverbs.scenario_status({"running": True, "name": "x", "speed": 2, "ok": None, "stopped": False,
                                      "steps": [{"at": 0, "target": "t", "do": {"secret": 1}, "status": "ok", "detail": ""},
                                                {"at": 5, "target": "t", "do": {}, "status": "pending", "detail": ""}],
                                      "expects": [{"label": "l", "status": "ok", "detail": ""}]})
    assert got["steps_total"] == 2 and got["steps_done"] == 1 and got["running"] is True
    assert "do" not in json.dumps(got["steps"])
    idle = agentverbs.scenario_status({"running": False})
    assert idle["name"] is None and idle["steps"] == [] and idle["running"] is False


def test_clear_faults_clears_every_node_and_the_exporter_for_gpu_nodes_only(monkeypatch):
    seen = []

    def execute(step, **kw):
        seen.append((step.target, step.do))
        return agentverbs.scenario.Outcome("x", "ok")
    monkeypatch.setattr(agentverbs.scenario, "execute", execute)
    got = agentverbs.clear_faults(SIM, None, None)
    assert got["ok"] is True
    assert seen == [("bmc-farm", {"nodes": ["cn001", "cn002", "gpu001"], "clear": True}), ("gpu-exporter", {"nodes": ["gpu001"], "clear": True})]


def test_clear_faults_not_ok_when_a_step_fails(monkeypatch):
    monkeypatch.setattr(agentverbs.scenario, "execute", lambda step, **kw: agentverbs.scenario.Outcome("x", "failed", "bmc-farm not reachable"))
    assert agentverbs.clear_faults(SIM, None, None)["ok"] is False


def test_capture_once_stops_at_the_message_limit():
    def run(sim, node, emit, seconds, stop=None):
        for i in range(10):
            if stop():
                break
            emit({"t": i})
    got = agentverbs.capture_once({}, "cn001", 10, 3, run=run)
    assert got["count"] == 3 and got["truncated"] is True and len(got["messages"]) == 3
    short = agentverbs.capture_once({}, "cn001", 10, 50, run=run)
    assert short["count"] == 10 and short["truncated"] is False


def test_cli_scenario_status_without_the_page_exits_1(monkeypatch):
    def down():
        raise OSError("refused")
    monkeypatch.setattr(cli, "_page_run_state", down)
    r = runner.invoke(cli.app, ["get", "scenario-status", "--json"])
    assert r.exit_code == 1 and "benchctl start" in r.output


def test_cli_capture_once_refused_by_the_governor_and_too_long(monkeypatch):
    monkeypatch.setattr(stack, "governor_admits", lambda need, say, ask=None: False)
    r = runner.invoke(cli.app, ["get", "capture", "cn001", "--once"])
    assert r.exit_code == 1 and "governor" in r.output
    monkeypatch.setattr(stack, "governor_admits", lambda need, say, ask=None: True)
    assert runner.invoke(cli.app, ["get", "capture", "cn001", "--once", "--seconds", "61"]).exit_code == 2


def test_clear_faults_cli_exit_follows_ok(monkeypatch):
    monkeypatch.setattr(agentverbs, "clear_faults", lambda *a: {"ok": False, "cleared": [{"target": "bmc-farm", "nodes": [], "status": "failed", "detail": "down"}]})
    monkeypatch.setattr(cli, "_sim", lambda: SIM)
    r = runner.invoke(cli.app, ["clear", "faults"])
    assert r.exit_code == 1 and "bmc-farm: failed (down)" in r.output
