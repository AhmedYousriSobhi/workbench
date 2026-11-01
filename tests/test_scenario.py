import urllib.error
from pathlib import Path

import pytest
import yaml

from benchctl import scenario

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
    assert ok.status == "ok" and [b["node"] for u, b in sent if "8000" in u] == ["cn001", "cn002"]
    assert sent[0][1] == {"node": "cn001", "fan_fail": 2}
    assert [b["kind"] for u, b in sent if "8080" in u] == ["fan", "fan"]  # and a ticket each

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
    assert sum(sleeps) == 5.0 and rep.ok  # 100s / (10 x 2), slept in 1 s slices


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


def test_ssh_step_commands_per_fault():
    cmds = scenario.ssh_commands({"state": "down"})
    assert cmds == [["docker", "stop", "-t", "1", "workbench-login-1"]]
    assert scenario.ssh_commands({"state": "hung"}) == [["docker", "pause", "workbench-login-1"]]
    assert scenario.ssh_commands({"state": "up"}) == [["docker", "unpause", "workbench-login-1"], ["docker", "start", "workbench-login-1"]]
    rot = scenario.ssh_commands({"rotate_host_key": True})
    assert len(rot) == 1 and "ssh-keygen" in rot[0][-1] and "kill -HUP" in rot[0][-1]
    lim = scenario.ssh_commands({"max_sessions": 2})[0][-1]
    assert "MaxSessions 2" in lim and "kill -HUP" in lim
    reset = scenario.ssh_commands({"reset": True})
    assert reset[0][1] == "unpause" and "/run/workbench/ssh/ssh_host_ed25519_key" in reset[-1][-1] and "MaxSessions" in reset[-1][-1]


def test_ssh_refused_key_and_single_node_sshd_commands():
    rej = scenario.ssh_commands({"reject_auth": True})
    assert len(rej) == 1 and rej[0][2] == "workbench-login-1" and "grep -v -e -id_ed25519" in rej[0][-1]
    assert "kill -HUP" not in rej[0][-1]  # authorized_keys is read at every login
    assert "authorized_keys" in scenario.ssh_commands({"reset": True})[-1][-1]  # reset gives the key back
    down = scenario.ssh_commands({"node_sshd": "down", "nodes": ["cn001", "cn002"]})
    assert [c[2] for c in down] == ["workbench-cn001-1", "workbench-cn002-1"] and "kill $(cat /run/sshd.pid)" in down[0][-1]
    assert "/usr/sbin/sshd" in scenario.ssh_commands({"node_sshd": "up", "nodes": ["cn001"]})[0][-1]


def test_execute_ssh_runs_the_commands_and_ignores_unpause_and_start_noise():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return CP(code=1, err="not paused") if cmd[1] in ("unpause", "start") else CP()

    assert scenario.execute(scenario.Step(0, "ssh", {"state": "up"}), runner=runner).status == "ok"
    assert [c[1] for c in calls] == ["unpause", "start"]
    bad = scenario.execute(scenario.Step(0, "ssh", {"state": "down"}), runner=lambda c: CP(code=1, err="no such container"))
    assert bad.status == "failed" and bad.detail == "no such container"


def test_ssh_steps_are_skipped_without_docker_and_validated():
    assert scenario.execute(scenario.Step(0, "ssh", {"state": "down"}), host=False).status == "skipped"
    sc = lambda do: {"steps": [{"at": "0s", "target": "ssh", "do": do}]}
    assert scenario.validate(sc({"state": "down"}), SIM) == []
    assert scenario.validate(sc({"max_sessions": 1, "rotate_host_key": True}), SIM) == []
    assert scenario.validate(sc({"reject_auth": True}), SIM) == []
    assert scenario.validate(sc({"node_sshd": "down", "nodes": ["cn001"]}), SIM) == []
    for bad in ({"node_sshd": "down"}, {"node_sshd": "hung", "nodes": ["cn001"]}, {"nodes": ["cn001"]}):
        assert scenario.validate(sc(bad), SIM), bad
    assert scenario.validate(sc({"node_sshd": "down", "nodes": ["nope"]}), SIM)
    for bad in ({"state": "slow"}, {"max_sessions": 0}, {"max_sessions": True}, {"bogus": 1}, {}):
        assert scenario.validate(sc(bad), SIM), bad


def test_jira_ticket_steps_post_to_the_ticket_endpoint_and_failure_steps_keep_theirs():
    sent = []
    poster = lambda url, body: sent.append((url, body))
    do = {"tickets": {"action": "create", "summary": "x"}}
    assert scenario.execute(scenario.Step(0, "jira", do), poster=poster).status == "ok"
    assert scenario.execute(scenario.Step(0, "jira", {"failure": {"mode": "503"}}), poster=poster).status == "ok"
    assert sent == [("http://127.0.0.1:8080/sim/tickets", {"action": "create", "summary": "x"}),
                    ("http://127.0.0.1:8080/sim/failure", {"mode": "503"})]


def test_validate_checks_ticket_steps():
    sim = yaml.safe_load(Path("sim.yaml").read_text())
    sc = {"steps": [{"at": "0s", "target": "jira", "do": {"tickets": {"action": "create"}}},
                    {"at": "0s", "target": "jira", "do": {"tickets": {"action": "transition", "jql": "x"}}},
                    {"at": "0s", "target": "jira", "do": {"tickets": {"action": "fly"}}},
                    {"at": "0s", "target": "jira-tickets", "do": {}},
                    {"at": "0s", "target": "jira", "do": {"tickets": {"action": "reset"}}}]}
    problems = scenario.validate(sc, sim)
    assert any("create needs summary" in p for p in problems) and any("transition needs to" in p for p in problems)
    assert any("action: one of" in p for p in problems) and any("unknown target 'jira-tickets'" in p for p in problems)
    assert not any(p.startswith("step 5") for p in problems)


def test_jira_expectations_check_labels_status_and_count(monkeypatch):
    issues = [{"fields": {"status": {"name": "Resolved"}}}, {"fields": {"status": {"name": "Resolved"}}}]
    monkeypatch.setattr(scenario, "jira_search", lambda jql: issues)
    _, holds, _ = scenario.expectation({"jira.issue_status": {"jql": "x", "status": "Resolved", "count": 2, "within": "5s"}})
    assert holds()
    _, holds, _ = scenario.expectation({"jira.issue_status": {"jql": "x", "status": "Closed", "within": "5s"}})
    assert not holds()
    _, holds, _ = scenario.expectation({"jira.issue_count": {"jql": "x", "count": 2}})
    assert holds()
    monkeypatch.setattr(scenario, "jira_search", lambda jql: [])
    _, holds, _ = scenario.expectation({"jira.issue_status": {"jql": "x", "status": "Resolved"}})
    assert not holds()  # nothing matched is not "all resolved"


def test_power_off_and_on_pause_and_unpause_the_node_container_and_tolerate_repeats():
    ran = []

    def runner(cmd):
        ran.append(cmd)
        return type("R", (), {"returncode": 1 if len(ran) == 3 else 0, "stdout": "", "stderr": "container is already paused" if len(ran) == 3 else ""})()
    ok = scenario.execute(scenario.Step(0, "slurm", {"power_off": ["cn001", "gpu002"]}), runner=runner)
    assert ok.status == "ok" and ran == [["docker", "pause", "workbench-cn001-1"], ["docker", "pause", "workbench-gpu002-1"]]
    scenario.execute(scenario.Step(0, "slurm", {"power_on": ["cn001"]}), runner=runner)
    assert ran[-1] == ["docker", "unpause", "workbench-cn001-1"]
    again = scenario.execute(scenario.Step(0, "slurm", {"power_off": ["cn001"]}), runner=runner)  # the third call: already paused
    assert again.status == "ok"
    bad = scenario.execute(scenario.Step(0, "slurm", {"power_off": ["cn001"]}), runner=lambda c: type("R", (), {"returncode": 1, "stdout": "", "stderr": "no such container"})())
    assert bad.status == "failed" and "no such container" in bad.detail
    assert scenario.validate({"steps": [{"at": "0s", "target": "slurm", "do": {"power_off": ["nope"]}}]}, SIM)


def test_a_bmc_power_step_also_sets_bcm_exporter_and_freezes_the_node():
    posts, ran = [], []
    runner = lambda cmd: ran.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    out = scenario.execute(scenario.Step(0, "bmc-farm", {"nodes": ["cn002"], "power": "Off"}), poster=lambda u, b: posts.append((u.split(":")[2][:4], b)), runner=runner)
    assert out.status == "ok"
    assert posts == [("8000", {"node": "cn002", "power": "Off"}), ("8081", {"node": "cn002", "state": "DOWN"}), ("9400", {"node": "cn002", "down": True}),
                     ("8080", {"action": "fault", "node": "cn002", "kind": "power", "detail": "BMC power Off"})]
    assert ran == [["docker", "pause", "workbench-cn002-1"]]
    posts.clear(); ran.clear()
    scenario.execute(scenario.Step(0, "bmc-farm", {"nodes": ["cn002"], "clear": True}), poster=lambda u, b: posts.append(b), runner=runner)
    assert {"node": "cn002", "state": "UP"} in posts and {"node": "cn002", "down": False} in posts and ran == [["docker", "unpause", "workbench-cn002-1"]]
    posts.clear(); ran.clear()  # other BMC changes imply nothing else but their ticket
    scenario.execute(scenario.Step(0, "bmc-farm", {"nodes": ["cn002"], "temp": 92}), poster=lambda u, b: posts.append(b), runner=runner)
    assert posts == [{"node": "cn002", "temp": 92}, {"action": "fault", "node": "cn002", "kind": "overheat", "detail": "CPU at 92 C"}] and ran == []


def test_without_docker_the_slurm_part_goes_through_the_callback_and_a_refusal_is_a_note_not_a_failure():
    asked = []
    def gov(node, state, reason):
        asked.append((node, state, reason))
        return state != "on", "refused at L3"
    out = scenario.execute(scenario.Step(0, "bmc-farm", {"nodes": ["cn002"], "power": "Off"}), poster=lambda u, b: None, host=False, slurm=gov)
    assert out.status == "ok" and asked == [("cn002", "off", None)]
    again = scenario.execute(scenario.Step(0, "bmc-farm", {"nodes": ["cn002"], "power": "On"}), poster=lambda u, b: None, host=False, slurm=gov)
    assert again.status == "ok" and "refused at L3" in again.detail
    d = scenario.execute(scenario.Step(0, "slurm", {"drain": ["gpu001"], "reason": "xid 79"}), host=False, slurm=gov)
    assert d.status == "ok" and asked[-1] == ("gpu001", "drain", "xid 79")
    assert scenario.execute(scenario.Step(0, "ssh", {"state": "hung"}), host=False, slurm=gov).status == "skipped"  # ssh needs Docker


def test_faults_file_tickets_and_recovery_resolves_them_and_no_jira_is_not_a_failure():
    def tickets(do, target="bmc-farm"):
        return [(e.do["tickets"]["kind"], e.do["tickets"].get("open", True)) for e in scenario.side_effects(scenario.Step(0, target, do))
                if e.target == "jira"]

    assert tickets({"nodes": ["cn001"], "power": "Off"}) == [("power", True)]
    assert tickets({"nodes": ["cn001"], "temp": 92, "fan_fail": 2}) == [("overheat", True), ("fan", True)]
    assert tickets({"nodes": ["cn001"], "temp": 60}) == []
    assert tickets({"nodes": ["cn001"], "power": "On"}) == [("power", False)] and tickets({"nodes": ["cn001"], "clear": True}) == [("", False)]
    assert tickets({"nodes": ["gpu001"], "xid": 79}, "gpu-exporter") == [("xid", True)]
    assert tickets({"nodes": ["gpu001"], "clear": True}, "gpu-exporter") == [("xid", False), ("ib", False), ("nic", False)]
    assert tickets({"nodes": ["cn001"], "ib_down": 1}, "gpu-exporter") == [("ib", True)] and tickets({"nodes": ["cn001"], "ib_down": 0}, "gpu-exporter") == [("ib", False)]
    assert tickets({"nodes": ["cn001"], "nic_flap": True}, "gpu-exporter") == [("nic", True)] and tickets({"nodes": ["cn001"], "nic_flap": False}, "gpu-exporter") == [("nic", False)]
    assert tickets({"nodes": ["gpu001"], "down": True}, "gpu-exporter") == []  # the exporter going quiet is a side effect, not a fault of its own

    def refuse_jira(url, body):
        if "8080" in url:
            raise OSError("refused")

    out = scenario.execute(scenario.Step(0, "gpu-exporter", {"nodes": ["gpu001"], "xid": 79}), poster=refuse_jira)
    assert out.status == "ok" and "no Jira" in out.detail


def test_a_jira_activity_step_switches_the_background_changes_and_is_validated():
    sent = []
    out = scenario.execute(scenario.Step(0, "jira", {"activity": {"on": True, "every": 5}}), poster=lambda u, b: sent.append((u, b)))
    assert out.status == "ok" and sent == [("http://127.0.0.1:8080/sim/activity", {"on": True, "every": 5})]


def _rec():
    ran = []

    def runner(cmd):
        ran.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    return ran, runner


def test_daemon_steps_become_docker_commands():
    ran, runner = _rec()
    scenario.execute(scenario.Step(0, "slurm", {"slurmd_stop": ["cn001"]}), runner=runner)
    assert ran == [["docker", "exec", "workbench-cn001-1", "touch", scenario.HOLD],
                   ["docker", "exec", "workbench-cn001-1", "pkill", "-x", "slurmd"]]
    ran.clear()
    scenario.execute(scenario.Step(0, "slurm", {"slurmd_start": ["cn001"], "slurmd_restart": ["cn002"]}), runner=runner)
    assert ["docker", "exec", "workbench-cn001-1", "rm", "-f", scenario.HOLD] in ran
    assert ["docker", "exec", "workbench-cn002-1", "pkill", "-x", "slurmd"] in ran
    ran.clear()
    scenario.execute(scenario.Step(0, "slurm", {"slurmctld_restart": True, "cancel": True}), runner=runner)
    assert ran[0][:2] == ["docker", "restart"] and "scancel" in ran[1]


def test_submit_makes_the_same_job_as_benchctl_submit_and_a_failed_submit_fails_the_step():
    ran, runner = _rec()
    sub = {"node": "gpu001", "gpus": 2, "duration": 40}
    assert scenario.execute(scenario.Step(0, "slurm", {"submit": sub}), runner=runner).status == "ok"
    assert ran[0][:5] == ["docker", "exec", "-u", "alice", "workbench-login-1"]
    assert "--nodelist=gpu001" in ran[0][-1] and "--gres=gpu:2" in ran[0][-1] and "sleep 40" in ran[0][-1]
    bad = scenario.execute(scenario.Step(0, "slurm", {"submit": {"node": "cn001"}}),
                           runner=lambda c: type("R", (), {"returncode": 1, "stdout": "", "stderr": "invalid account"})())
    assert bad.status == "failed" and "invalid account" in bad.detail
    # pkill finding nothing (exit 1) is the goal of a stop, not a failure
    none = scenario.execute(scenario.Step(0, "slurm", {"slurmd_stop": ["cn001"]}),
                            runner=lambda c: type("R", (), {"returncode": 1 if "pkill" in c else 0, "stdout": "", "stderr": ""})())
    assert none.status == "ok"


def test_without_docker_daemon_steps_go_through_the_daemon_callback_and_a_refusal_is_a_skip():
    asked = []
    o = scenario.execute(scenario.Step(0, "slurm", {"slurmd_restart": ["cn001"]}), host=False,
                         daemon=lambda do: asked.append(do) or (True, "asked the governor"))
    assert o.status == "ok" and asked == [{"slurmd_restart": ["cn001"]}]
    refused = scenario.execute(scenario.Step(0, "slurm", {"cancel": True}), host=False, daemon=lambda do: (False, "refused at L3"))
    assert refused.status == "skipped" and "L3" in refused.detail
    seen = []
    scenario.run({"name": "x", "steps": [{"at": "0s", "target": "slurm", "do": {"cancel": True}}]}, host=False,
                 daemon=lambda do: (True, ""), on_before=seen.append, say=lambda m: None)
    assert len(seen) == 1


def test_daemon_steps_need_the_host_and_are_validated():
    o = scenario.execute(scenario.Step(0, "slurm", {"slurmd_restart": ["cn001"]}), host=False, slurm=lambda *a: (True, ""))
    assert o.status == "skipped"
    bad = {"steps": [{"at": "0s", "target": "slurm", "do": {"slurmd_stop": ["nope"], "submit": {"node": "cn001", "x": 1}}},
                     {"at": "1s", "target": "slurm", "do": {"submit": "cn001"}}]}
    problems = scenario.validate(bad, SIM)
    assert len(problems) == 3 and any("nope" in p for p in problems) and any("unknown key" in p for p in problems)
    assert scenario.disruptive(bad) and not scenario.disruptive({"steps": [{"at": "0s", "target": "slurm", "do": {"drain": ["cn001"]}}]})


def test_job_and_stepd_expectations():
    out = {"squeue": "RUNNING\n", "pgrep": ""}

    def runner(cmd):
        key = "squeue" if "squeue" in cmd else "pgrep"
        return type("R", (), {"returncode": 0 if out[key] or key == "squeue" else 1, "stdout": out[key], "stderr": ""})()
    _, holds, _ = scenario.expectation({"slurm.job_state": {"node": "cn001", "state": "RUNNING"}}, runner)
    assert holds()
    _, holds, _ = scenario.expectation({"slurm.stepd": {"node": "cn001", "alive": False, "within": "5s"}}, runner)
    assert holds()  # pgrep found nothing
    _, holds, _ = scenario.expectation({"slurm.stepd": {"node": "cn001"}}, runner)
    assert not holds()
