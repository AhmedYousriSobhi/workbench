import urllib.error
from pathlib import Path

import pytest
import yaml

from simctl import scenario

SIM = yaml.safe_load(Path("sim.yaml").read_text())


class CP:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


def test_parse_duration():
    assert [scenario.parse_duration(x) for x in ("30s", "2m", "1h", 45, "1.5m")] == [30, 120, 3600, 45, 90]
    with pytest.raises(scenario.ScenarioError):
        scenario.parse_duration("soon")


def test_every_shipped_scenario_is_valid():
    for sc in scenario.list_all():
        assert scenario.validate(sc, SIM) == [], sc["name"]


def test_validate_flags_unknown_target_node_and_time():
    sc = {"steps": [{"at": "x", "target": "nope", "do": {}}, {"at": "1s", "target": "bmc-farm", "do": {"nodes": ["gpu999"]}}]}
    problems = scenario.validate(sc, SIM)
    assert len(problems) == 3 and any("gpu999" in p for p in problems)


def test_plan_orders_steps_and_expands_a_ramp():
    sc = {"steps": [{"at": "30s", "target": "bmc-farm", "do": {"nodes": ["gpu001"], "ramp": {"to": 90, "over": "20s"}}},
                    {"at": "0s", "target": "slurm", "do": {"drain": ["gpu001"]}}]}
    steps = scenario.plan(sc)
    assert [s.at for s in steps] == [0, 40, 50]
    assert [s.do.get("temp") for s in steps[1:]] == [70.0, 90.0]  # from 50C to 90C, no empty step for the ramp itself


def test_execute_posts_to_each_node_and_reports_http_errors():
    sent = []
    ok = scenario.execute(scenario.Step(0, "bmc-farm", {"nodes": ["cn001", "cn002"], "fan_fail": 2}),
                          poster=lambda url, body: sent.append((url, body)))
    assert ok.status == "ok" and [b["node"] for _, b in sent] == ["cn001", "cn002"]
    assert sent[0][1] == {"node": "cn001", "fan_fail": 2}

    def refuse(url, body):
        raise urllib.error.HTTPError(url, 400, "bad", {}, None)

    assert scenario.execute(scenario.Step(0, "bcm-mock", {"nodes": ["x"]}), poster=refuse).status == "failed"

    def down(url, body):
        raise ConnectionRefusedError

    assert "unreachable" in scenario.execute(scenario.Step(0, "jira", {"failure": {"mode": "429"}}), poster=down).detail


def test_execute_slurm_drain_and_resume_use_scontrol():
    calls = []
    runner = lambda cmd: calls.append(cmd) or CP()
    scenario.execute(scenario.Step(0, "slurm", {"drain": ["cn001"], "reason": "r"}), runner=runner)
    scenario.execute(scenario.Step(0, "slurm", {"resume": ["cn001"]}), runner=runner)
    assert "state=drain" in calls[0] and "reason=r" in calls[0] and "state=resume" in calls[1]
    assert scenario.execute(scenario.Step(0, "slurm", {"drain": ["cn001"]}), runner=lambda c: CP(code=1, err="boom")).detail == "boom"


def test_unbuilt_targets_are_skipped_not_failed():
    assert scenario.execute(scenario.Step(0, "ticket-bot", {})).status == "skipped"


def test_run_waits_scaled_by_speed_and_dilation_and_stops_clean():
    now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    sc = {"name": "t", "steps": [{"at": "100s", "target": "jira", "do": {"failure": {"mode": "429"}}}]}
    rep = scenario.run(sc, speed=10, dilation=2, say=lambda m: None, sleep=sleep, clock=lambda: now[0], poster=lambda u, b: None)
    assert sleeps == [5.0] and rep.ok  # 100s / (10 x 2)


def test_expect_polls_until_it_holds_then_fails_after_the_deadline():
    now = [0.0]
    states = iter(["idle", "idle", "drained"])
    runner = lambda cmd: CP(next(states))

    def sleep(s):
        now[0] += s

    exp = {"slurm.node_state": {"node": "cn001", "state": "drain", "within": "10s"}}
    assert scenario.check_expect(exp, 1, sleep, lambda: now[0], runner).status == "ok"
    never = lambda cmd: CP("idle")
    late = scenario.check_expect(exp, 1, sleep, lambda: now[0], never)
    assert late.status == "failed" and "within" in late.detail
    assert scenario.check_expect({"k8s.node_condition": {"within": "1s"}}, 1, sleep, lambda: now[0]).status == "skipped"


def test_run_passes_an_expectation_that_held_even_if_a_later_step_undid_it():
    now = [0.0]
    drained = [False]

    def sleep(s):
        now[0] += s

    def runner(cmd):
        if "state=drain" in cmd:
            drained[0] = True
        elif "state=resume" in cmd:
            drained[0] = False
        return CP("drained" if drained[0] and "sinfo" in cmd else "idle")

    sc = {"name": "t", "steps": [{"at": "0s", "target": "slurm", "do": {"drain": ["cn001"]}},
                                 {"at": "30s", "target": "slurm", "do": {"resume": ["cn001"]}}],
          "expect": [{"slurm.node_state": {"node": "cn001", "state": "drain", "within": "20s"}}]}
    rep = scenario.run(sc, say=lambda m: None, sleep=sleep, clock=lambda: now[0], runner=runner)
    assert rep.ok and [e.status for e in rep.expects] == ["ok"]


def test_run_fails_an_expectation_that_never_holds():
    now = [0.0]
    sc = {"name": "t", "steps": [], "expect": [{"slurm.node_state": {"node": "cn001", "state": "drain", "within": "5s"}}]}
    rep = scenario.run(sc, say=lambda m: None, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0],
                       runner=lambda c: CP("idle"))
    assert not rep.ok and rep.expects[0].status == "failed"


def test_a_failed_step_fails_the_report_but_a_skip_does_not():
    r = scenario.Report("x", steps=[scenario.Outcome("a", "ok"), scenario.Outcome("b", "skipped")])
    assert r.ok
    r.expects.append(scenario.Outcome("e", "failed"))
    assert not r.ok
