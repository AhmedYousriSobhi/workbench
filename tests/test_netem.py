"""In-cluster netem (issue 478, ADR 0056): validation, command construction, TTL, L0-L1 gate, user-mode refusal. No tc ever runs."""
import json
from pathlib import Path

import pytest
import yaml

from benchctl import netemfaults, scenario
from governor import control, netem
from governor.netem import Netem

NODES = {"cn001", "gpu001"}


def inspect(labels=None, pid=4242, running=True, mode="bridge"):
    labels = {"com.docker.compose.project": "workbench", "workbench.node": "compute"} if labels is None else labels
    return json.dumps([{"Config": {"Labels": labels}, "State": {"Pid": pid, "Running": running}, "HostConfig": {"NetworkMode": mode}}])


class Host:
    """A fake host: records every command, answers docker inspect / ip link / tc qdisc show."""

    def __init__(self, info=None, qdisc="", fail_tc=False):
        self.calls, self.info, self.qdisc, self.fail_tc = [], info or inspect(), qdisc, fail_tc

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[:2] == ["docker", "inspect"]:
            return (0, self.info, "") if cmd[-1].startswith("workbench-") else (0, "4242\n", "")
        if cmd[:2] == ["docker", "ps"]:
            return 0, "abc\ndef\n", ""
        if cmd[-4:] == ["ip", "-o", "link", "show"]:
            return 0, "1: lo: <LOOPBACK>\n7: eth0@if8: <UP>\n9: eth1@if10: <UP>\n11: tunl0@NONE: <X>\n", ""
        if "show" in cmd and "tc" in cmd:
            return 0, self.qdisc, ""
        if "replace" in cmd and self.fail_tc:
            return 2, "", "RTNETLINK answers: Operation not permitted"
        return 0, "", ""

    def tc(self):
        return [c for c in self.calls if "tc" in c]


def make(host=None, level=0):
    log, clock = [], {"t": 0.0}
    n = Netem(host or Host(), lambda: level, log.append, clock=lambda: clock["t"])
    return n, log, clock


def test_parsers_and_bounds_one_message_per_condition():
    assert netem.parse_delay("200ms") == 200 and netem.parse_delay("1s") == 1000 and netem.parse_delay("soon") is None
    assert netem.parse_loss("5%") == 5.0 and netem.parse_loss("0.5%") == 0.5 and netem.parse_loss("5") is None
    assert netem.parse_ttl("60s") == 60 and netem.parse_ttl("5m") == 300 and netem.parse_ttl("1h") is None
    assert netem.problems(200, 5, 60) == [] and netem.problems(None, 5, None) == []
    assert len(netem.problems(None, None, 60)) == 1
    assert len(netem.problems(501, 21, 601)) == 3
    assert netem.problems(0, None, 60) and netem.problems(True, None, 60) and netem.problems(10, None, 0)


def test_set_builds_tc_inside_the_container_namespace_only():
    host = Host()
    n, log, _ = make(host)
    r = n.set("cn001", 200, 5, 60)
    assert r == {"ok": True, "node": "cn001", "ttl_s": 60}
    sets = [c for c in host.tc() if "replace" in c]
    assert sets == [["nsenter", "-t", "4242", "-n", "tc", "qdisc", "replace", "dev", dev, "root", "netem", "delay", "200ms", "loss", "5%"] for dev in ("eth0", "eth1")]
    assert all(c[0] in ("docker", "nsenter") for c in host.calls)  # never a bare host `tc`, so no host interface
    assert n.status()[0]["node"] == "cn001" and "netem set cn001" in log[0]


def test_refuses_targets_outside_the_cluster():
    for info, text in ((inspect(labels={}), "not a Slurm node"), (inspect(labels={"workbench.node": "x"}), "not a Slurm node"),
                       (inspect(mode="host"), "not a Slurm node"), (inspect(running=False, pid=0), "not running")):
        n, _, _ = make(Host(info))
        r = n.set("cn001", 10, None, 5)
        assert not r["ok"] and text in r["error"]
    n, _, _ = make(lambda cmd: (1, "", "no such object"))
    assert "no container workbench-cn001-1" in n.set("cn001", 10, None, 5)["error"]


def test_level_gate_and_tc_failure_leave_nothing():
    n, _, _ = make(level=2)
    assert "L0-L1" in n.set("cn001", 10, None, 5)["error"] and not n.active
    host = Host(fail_tc=True, qdisc="qdisc netem 8001: root refcnt 2 limit 1000 delay 10ms")
    n, _, _ = make(host)
    r = n.set("cn001", 10, None, 5)
    assert not r["ok"] and "tc failed" in r["error"] and not n.active
    assert any("del" in c for c in host.tc())  # a half-applied rule is removed


def test_ttl_expiry_clears_and_clear_is_allowed_under_pressure():
    host = Host(qdisc="qdisc netem 8001: root refcnt 2 limit 1000 delay 10ms")
    n, log, clock = make(host)
    n.set("cn001", 10, None, 30)
    clock["t"] = 29
    assert n.expire() == []
    clock["t"] = 31
    assert n.expire() == ["cn001"] and not n.active
    assert any(c[-6:] == ["qdisc", "del", "dev", "eth1", "root"][-6:] or "del" in c for c in host.tc())
    assert "ttl ended" in log[-1]
    n2, _, _ = make(host, level=4)  # clearing only removes load: every level
    n2.active["cn001"] = {"deadline": 99, "pid": 4242, "delay_ms": 1, "loss_pct": None}
    assert n2.clear()["ok"] and not n2.active


def test_clear_all_at_start_removes_only_netem_roots():
    host = Host(qdisc="qdisc fq_codel 0: root refcnt 2")  # someone else's qdisc stays
    n, _, _ = make(host)
    assert n.clear_all("governor start") == 2
    assert not any("del" in c for c in host.tc())
    host2 = Host(qdisc="qdisc netem 8001: root refcnt 2 limit 1000 delay 10ms")
    make(host2)[0].clear_all()
    assert sum("del" in c for c in host2.tc()) == 4  # two containers, eth0 and eth1 each


def test_control_validates_before_the_governor_sees_it():
    seen = []
    h = lambda cmd, req: seen.append(req) or {"ok": True}
    d = lambda req: control.dispatch(json.dumps(req).encode(), h, lambda: set())
    assert d({"cmd": "netem-set", "node": "cn001", "delay_ms": 200, "ttl_s": 60})["ok"]
    for bad in ({"node": "cn001; rm", "delay_ms": 10, "ttl_s": 5}, {"node": "cn001", "delay_ms": 10 ** 6, "ttl_s": 5},
                {"node": "cn001", "loss_pct": 90, "ttl_s": 5}, {"node": "cn001", "delay_ms": 10, "ttl_s": 10 ** 6}, {"node": "cn001", "ttl_s": 5},
                {"node": "eth0", "delay_ms": 10}):
        assert not d({"cmd": "netem-set", **bad})["ok"]
    assert d({"cmd": "netem-clear"})["ok"] and d({"cmd": "netem-clear", "node": "cn001"})["ok"]
    assert not d({"cmd": "netem-clear", "node": "x y"})["ok"]
    assert len(seen) == 3


def test_scenario_and_cli_validation_and_requests():
    ok = {"nodes": ["cn001"], "delay": "200ms", "loss": "5%", "ttl": "60s"}
    assert netemfaults.problems(ok, NODES) == [] and netemfaults.problems({"clear": True}, NODES) == []
    text = "\n".join(netemfaults.problems({"nodes": ["cn999"], "delay": "9s", "loss": "50%", "ttl": "2h", "bogus": 1}, NODES))
    assert "unknown netem key 'bogus'" in text and "unknown node 'cn999'" in text and "ttl '2h'" in text
    assert any("1..500" in m for m in netemfaults.problems({"nodes": ["cn001"], "delay": "5000ms"}, NODES))
    assert any("0.1..20" in m for m in netemfaults.problems({"nodes": ["cn001"], "loss": "50%"}, NODES))
    assert netemfaults.problems({"nodes": ["cn001"]}, NODES) and netemfaults.problems({"delay": "1ms"}, NODES)
    assert netemfaults.problems({"clear": True, "delay": "1ms"}, NODES)
    sent = []
    assert netemfaults.apply(ok, lambda r: sent.append(r) or {"ok": True}) == ""
    assert sent == [{"cmd": "netem-set", "node": "cn001", "ttl_s": 60, "delay_ms": 200, "loss_pct": 5.0}]
    assert netemfaults.apply({"nodes": ["cn001"], "delay": "1ms"}, lambda r: sent.append(r) or {"ok": True}) == "" and sent[-1]["ttl_s"] == 60  # TTL default
    assert netemfaults.apply({"clear": True}, lambda r: sent.append(r) or {"ok": True}) == "" and sent[-1] == {"cmd": "netem-clear"}
    assert netemfaults.apply(ok, lambda r: {"ok": False, "error": netem.USER_MODE_MSG}) == netem.USER_MODE_MSG
    assert "reinstall the root service" in netemfaults.apply(ok, lambda r: {"ok": False, "error": "unknown command: 'netem-set'"})

    def gone(r):
        raise OSError("no socket")
    assert "install-governor.sh" in netemfaults.apply(ok, gone)
    assert netemfaults.apply({"nodes": ["cn001"], "delay": "5000ms"}, lambda r: pytest.fail("must not ask")) != ""


def test_shipped_scenario_validates_and_the_page_cannot_play_it():
    sc = scenario.load("netem-slow-link")
    sim = yaml.safe_load(Path("sim.yaml").read_text())
    assert scenario.validate(sc, sim) == []
    assert "netem" in scenario.HOST_TARGETS
    steps = scenario.plan(sc)
    assert all("host CLI" in scenario.page_support(s) for s in steps)
    out = scenario.execute(steps[0], host=False)
    assert out.status == "skipped"  # the page's player has no Docker or root: never runs netem (W-3)
    bad = {"name": "x", "steps": [{"at": "0s", "target": "netem", "do": {"nodes": ["cn001"], "delay": "5s"}}]}
    assert any("delay" in m for m in scenario.validate(bad, sim))


def test_execute_on_the_host_goes_through_the_governor_only(monkeypatch):
    sent = []
    monkeypatch.setattr(netemfaults, "_ask", lambda req, ask=None: sent.append(req) or {"ok": True})
    o = scenario.execute(scenario.Step(0, "netem", {"nodes": ["cn001"], "delay": "10ms", "ttl": "5s"}), host=True)
    assert o.status == "ok" and sent[0]["cmd"] == "netem-set"
    monkeypatch.setattr(netemfaults, "_ask", lambda req, ask=None: {"ok": False, "error": netem.USER_MODE_MSG})
    o = scenario.execute(scenario.Step(0, "netem", {"nodes": ["cn001"], "delay": "10ms"}), host=True)
    assert o.status == "failed" and "install-governor.sh" in o.detail


def test_cli_verbs(monkeypatch):
    from typer.testing import CliRunner

    from benchctl.cli import app
    sent = []
    monkeypatch.setattr(netemfaults, "_ask", lambda req, ask=None: sent.append(req) or {"ok": True})
    r = CliRunner().invoke(app, ["set", "netem", "cn001", "--delay", "200ms", "--loss", "5%", "--ttl", "60s"])
    assert r.exit_code == 0 and sent[-1]["delay_ms"] == 200, r.output
    r = CliRunner().invoke(app, ["set", "netem", "cn001", "--delay", "9000ms"])
    assert r.exit_code == 1 and "delay" in r.output
    r = CliRunner().invoke(app, ["set", "netem", "cn001"])
    assert r.exit_code == 1
    r = CliRunner().invoke(app, ["clear", "netem"])
    assert r.exit_code == 0 and sent[-1] == {"cmd": "netem-clear"}
    r = CliRunner().invoke(app, ["clear", "netem", "cn001"])
    assert sent[-1] == {"cmd": "netem-clear", "node": "cn001"}
    monkeypatch.setattr(netemfaults, "_ask", lambda req, ask=None: {"ok": False, "error": netem.USER_MODE_MSG})
    r = CliRunner().invoke(app, ["set", "netem", "cn001", "--delay", "10ms"])
    assert r.exit_code == 1 and "install-governor.sh" in r.output
