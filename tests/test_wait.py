"""benchctl wait (SPEC AG-7, ADR 0066). Hermetic: a fake clock, fake readers."""
import json

from typer.testing import CliRunner

from benchctl import alerts, cli, inventory, wait

runner = CliRunner()


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(check, timeout=10, interval=2.0):
    c = Clock()
    return wait.poll(check, timeout, interval, sleep=c.sleep, clock=c.now)


def test_met_after_a_few_polls():
    calls = []

    def check():
        calls.append(1)
        return len(calls) >= 3, {"n": len(calls)}
    r = run(check)
    assert r["outcome"] == "met" and r["ok"] and r["elapsed_s"] == 4.0 and r["seen"] == {"n": 3}


def test_timeout_and_failed_and_precondition():
    assert run(lambda: (False, {"x": 1}), timeout=5)["outcome"] == "timeout"
    assert run(lambda: (None, {}))["outcome"] == "failed"

    def bad():
        raise wait.PreconditionError("no prometheus", "WB-PART-NOT-RUNNING", "benchctl start")
    r = run(bad)
    assert r["outcome"] == "precondition" and r["code"] == "WB-PART-NOT-RUNNING" and not r["ok"]
    assert wait.EXIT == {"met": 0, "timeout": 1, "failed": 1, "precondition": 2}


def test_it_never_sleeps_past_the_timeout():
    c = Clock()
    wait.poll(lambda: (False, {}), 3, 2.0, sleep=c.sleep, clock=c.now)
    assert c.t == 3.0


def test_alert_check_needs_prometheus_and_reads_states():
    def down(url, timeout=2.0):
        raise OSError("refused")
    try:
        wait.alert_check("A", "firing", down)()
        raise AssertionError("must raise")
    except wait.PreconditionError as e:
        assert e.code == "WB-PART-NOT-RUNNING" and "benchctl start" in str(e)

    def up(url, timeout=2.0):
        return {"data": {"alerts": [{"labels": {"alertname": "A", "severity": "page"}, "state": "firing", "activeAt": "t"}]}}
    assert wait.alert_check("A", "firing", up)()[0] is True
    assert wait.alert_check("B", "firing", up)()[0] is False
    assert wait.alert_check("A", "resolved", up)()[0] is False
    assert wait.alert_check("B", "resolved", up)()[0] is True
    assert alerts.fetch(up)[0]["severity"] == "page"


def test_scenario_check_waits_for_that_scenario_to_end():
    st = {"running": True, "name": "node-down", "ok": None}
    check = wait.scenario_check("node-down", lambda: st)
    assert check()[0] is False
    st.update(running=False, ok=True)
    assert check()[0] is True
    st.update(ok=False)
    assert check()[0] is None  # ended failed
    assert wait.scenario_check("other", lambda: st)()[0] is False  # another scenario ran: not started yet


def test_scenario_check_without_the_page_is_a_precondition():
    def down():
        raise OSError("refused")
    try:
        wait.scenario_check("x", down)()
        raise AssertionError("must raise")
    except wait.PreconditionError as e:
        assert e.fix == "benchctl start"


def test_ready_check():
    answers = {"slurmctld": True, "bmc-farm": False}
    check = wait.ready_check(lambda: ["slurmctld", "bmc-farm", "grafana"], lambda n: answers[n])
    assert check() == (False, {"running": 3, "not_answering": ["bmc-farm"]})
    answers["bmc-farm"] = True
    assert check()[0] is True
    try:
        wait.ready_check(list, lambda n: True)()
        raise AssertionError("must raise")
    except wait.PreconditionError as e:
        assert e.code == "WB-STACK-NOT-RUNNING"


def test_cli_alert_without_prometheus_exits_2_with_one_json_object(monkeypatch):
    def down(get=None):
        raise alerts.NotRunning("Prometheus does not answer at x")
    monkeypatch.setattr(alerts, "fetch", down)
    r = runner.invoke(cli.app, ["wait", "alert", "A", "--timeout", "1"])
    assert r.exit_code == 2
    data = json.loads(r.stdout)
    assert data["outcome"] == "precondition" and data["code"] == "WB-PART-NOT-RUNNING" and data["fix"] == "benchctl start"


def test_cli_node_state_met_and_timeout(monkeypatch):
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"cn001": "running"})
    r = runner.invoke(cli.app, ["wait", "node-state", "cn001", "running", "--timeout", "1"])
    assert r.exit_code == 0 and json.loads(r.stdout)["outcome"] == "met"
    monkeypatch.setattr(wait, "INTERVAL", 0.01)
    r = runner.invoke(cli.app, ["wait", "node-state", "cn001", "paused", "--timeout", "1"])
    assert r.exit_code == 1 and json.loads(r.stdout)["seen"] == {"node": "cn001", "state": "running"}


def test_cli_get_alerts_without_prometheus_exits_1(monkeypatch):
    def down(get=None):
        raise alerts.NotRunning("Prometheus does not answer at x")
    monkeypatch.setattr(alerts, "fetch", down)
    r = runner.invoke(cli.app, ["get", "alerts", "--json"])
    assert r.exit_code == 1 and r.stdout == ""
