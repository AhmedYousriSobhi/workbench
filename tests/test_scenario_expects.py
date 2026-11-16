"""Expectations are relative to the run (issues 833, 836, 839): `after`, `at_end`, `exact`, and Jira tickets of this run only."""
from pathlib import Path

import pytest
import yaml

from benchctl import scenario

SIM = yaml.safe_load(Path("sim.yaml").read_text())


class CP:
    def __init__(self, out="", code=0):
        self.stdout, self.returncode, self.stderr = out, code, ""


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, s):
        self.now += s

    def __call__(self):
        return self.now


def play(sc, runner=lambda c: CP("idle"), poster=lambda u, b: None, clock=None):
    clock = clock or Clock()
    return scenario.run(sc, say=lambda m: None, sleep=clock.sleep, clock=clock, runner=runner, poster=poster)


def fault_at(clock, at):
    """A runner that reports `idle` until `at` seconds, then `down*`: the node a fault took down."""
    return lambda cmd: CP("down*" if clock.now >= at else "idle")


NODE_IDLE = {"slurm.node_state": {"node": "cn001", "state": "idle"}}
STEP = {"at": "10s", "target": "jira", "do": {"failure": {"mode": None}}}


# ---- validation -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("exp,fragment", [
    ({"slurm.node_state": {"node": "cn001", "state": "idle", "after": "soon"}}, "after 'soon' is not a duration"),
    ({"slurm.node_state": {"node": "cn001", "state": "idle", "within": "x"}}, "within 'x' is not a duration"),
    ({"slurm.node_state": {"node": "cn001", "state": "idle", "at_end": "yes"}}, "at_end is true or false"),
    ({"slurm.node_state": {"node": "cn001", "state": "idle", "exact": 1}}, "exact is true or false"),
    ({"slurm.node_state": {"node": "cn001", "state": "idle", "after": "5s", "at_end": True}}, "after or at_end, not both"),
    ({"slurm.stepd": {"node": "cn001", "exact": True}}, "exact only applies to slurm.node_state"),
    ({"slurm.stepd": "cn001"}, "one assertion with its arguments"),
    ({"a": {}, "b": {}}, "one assertion with its arguments"),
])
def test_validate_rejects_a_bad_expect(exp, fragment):
    problems = scenario.validate({"steps": [], "expect": [exp]}, SIM)
    assert len(problems) == 1 and problems[0].startswith("expect 1: ") and fragment in problems[0]


def test_validate_accepts_the_new_arguments():
    ok = [{"slurm.node_state": {"node": "cn001", "state": "draining", "exact": True, "after": "20s", "within": "30s"}},
          {"slurm.stepd": {"node": "cn001", "at_end": True}}, {"slurm.stepd": {"node": "cn001", "after": 30}}]
    assert scenario.validate({"steps": [], "expect": ok}, SIM) == []


def test_every_shipped_scenario_still_validates_and_the_survive_ones_say_after():
    sim = {**SIM, "topology": {"plugin": "tree", "spines": 1, "leaves_per_spine": 2}}
    names = {sc["name"]: sc for sc in scenario.list_all(sim=sim)}
    for sc in names.values():
        assert scenario.validate(sc, sim) == [], sc["name"]
    for name in ("slurmd-restart-mid-job", "slurmctld-restart-mid-job", "slurm-upgrade-mid-job", "k8s-drain-blocked", "slurmd-disabled-mid-job"):
        late = [next(iter(e.values())) for e in names[name]["expect"] if "after" in next(iter(e.values()))]
        assert late, name
        fault = min(s.at for s in scenario.plan(names[name]) if s.do.get("slurmd_restart") or s.do.get("slurmd_stop") or s.do.get("slurmctld_restart")
                    or s.target in ("slurm-upgrade", "k8s"))
        assert all(scenario.parse_duration(a["after"]) > fault for a in late), name  # looked at only once the fault is in


def test_slurmd_disabled_does_not_claim_the_job_survives():
    sc = scenario.load("slurmd-disabled-mid-job", sim=SIM)
    late = [next(iter(e.items())) for e in sc["expect"] if "after" in next(iter(e.values()))]
    assert late and all(kind == "slurm.node_state" and args["state"] == "down" for kind, args in late)


# ---- after / at_end ---------------------------------------------------------------------------------------------

def test_without_after_a_check_that_holds_before_the_fault_passes_early():
    """The bug of issue 833: the node is `idle` until the fault at 10 s, and a check that it is idle passes at once."""
    clock = Clock()
    rep = play({"name": "t", "steps": [STEP], "expect": [{"slurm.node_state": {"node": "cn001", "state": "idle", "within": "20s"}}]},
               fault_at(clock, 10), clock=clock)
    assert rep.ok


def test_after_looks_only_once_the_time_has_come_so_a_fault_can_fail_it():
    clock = Clock()
    exp = {"slurm.node_state": {"node": "cn001", "state": "idle", "after": "20s"}}
    rep = play({"name": "t", "steps": [STEP], "expect": [exp]}, fault_at(clock, 10), clock=clock)
    assert [e.status for e in rep.expects] == ["failed"] and clock.now >= 20  # no pass at 1 s


def test_after_passes_when_the_state_holds_at_that_time():
    clock = Clock()
    seen = []

    def runner(cmd):
        seen.append(clock.now)
        return CP("mixed")
    exp = {"slurm.node_state": {"node": "cn001", "state": "mixed", "after": "20s"}}
    rep = play({"name": "t", "steps": [STEP], "expect": [exp]}, runner, clock=clock)
    assert rep.ok and min(seen) >= 20  # never asked before


def test_within_counts_from_after_not_from_the_start():
    clock = Clock()
    # the node comes up 25 s in: after 20 s with 10 s to hold passes at 25 s; with within 3 s it would not
    runner = lambda cmd: CP("idle" if clock.now >= 25 else "down*")
    rep = play({"name": "t", "steps": [STEP], "expect": [{"slurm.node_state": {"node": "cn001", "state": "idle", "after": "20s", "within": "10s"}}]},
               runner, clock=clock)
    assert rep.ok
    clock = Clock()
    rep = play({"name": "t", "steps": [STEP], "expect": [{"slurm.node_state": {"node": "cn001", "state": "idle", "after": "20s", "within": "3s"}}]},
               lambda cmd: CP("idle" if clock.now >= 25 else "down*"), clock=clock)
    assert not rep.ok


def test_after_is_scaled_by_speed_like_a_step_time():
    clock = Clock()
    seen = []

    def runner(cmd):
        seen.append(clock.now)
        return CP("idle")
    scenario.run({"name": "t", "steps": [STEP], "expect": [{"slurm.node_state": {"node": "cn001", "state": "idle", "after": "20s"}}]},
                 speed=10, say=lambda m: None, sleep=clock.sleep, clock=clock, runner=runner, poster=lambda u, b: None)
    assert min(seen) >= 2.0 and max(seen) < 3.0  # 20 s / 10


def test_at_end_is_not_looked_at_until_the_last_step_has_run():
    clock = Clock()
    asked = []

    def runner(cmd):
        asked.append(clock.now)
        return CP("idle")
    sc = {"name": "t", "steps": [{"at": "5s", "target": "jira", "do": {"failure": {"mode": None}}}, {"at": "40s", "target": "jira", "do": {"failure": {"mode": None}}}],
          "expect": [{"slurm.node_state": {"node": "cn001", "state": "idle", "at_end": True}}]}
    rep = play(sc, runner, clock=clock)
    assert rep.ok and min(asked) >= 40


def test_at_end_fails_when_the_state_is_wrong_at_the_end_and_within_counts_from_then():
    clock = Clock()
    sc = {"name": "t", "steps": [STEP], "expect": [{"slurm.node_state": {"node": "cn001", "state": "idle", "at_end": True, "within": "5s"}}]}
    rep = play(sc, lambda cmd: CP("down*"), clock=clock)
    assert not rep.ok and clock.now >= 15  # 10 s step + 5 s window
    clock = Clock()
    rep = play({**sc, "expect": [{"slurm.node_state": {"node": "cn001", "state": "idle", "at_end": True, "within": "5s"}}]},
               lambda cmd: CP("idle" if clock.now >= 13 else "down*"), clock=clock)
    assert rep.ok


def test_at_end_with_no_steps_starts_at_once():
    clock = Clock()
    rep = play({"name": "t", "steps": [], "expect": [{"slurm.node_state": {"node": "cn001", "state": "idle", "at_end": True}}]}, clock=clock)
    assert rep.ok


# ---- exact node state ---------------------------------------------------------------------------------------------

def test_node_state_is_a_substring_unless_exact():
    def holds(args, out):
        return scenario.expectation({"slurm.node_state": {"node": "cn001", **args}}, lambda c: CP(out))[1]()
    assert holds({"state": "drain"}, "draining") and holds({"state": "drain"}, "drained") and holds({"state": "down"}, "down*")
    assert not holds({"state": "drain", "exact": True}, "draining")
    assert holds({"state": "draining", "exact": True}, "draining") and not holds({"state": "draining", "exact": True}, "drained")
    assert holds({"state": "down*", "exact": True}, "down*") and not holds({"state": "down", "exact": True}, "down*")


# ---- Jira: tickets of this run only -------------------------------------------------------------------------------

def ticket(key, created, status="Open"):
    return {"key": key, "fields": {"created": created, "status": {"name": status}}}


@pytest.fixture
def mock(monkeypatch):
    """A Jira mock with the tickets of an earlier run in it; a step of the scenario adds more."""
    box = {"issues": [ticket(f"OPS-{n}", "2026-11-16T09:00:00.000+0000", "Resolved") for n in range(1, 13)]}
    monkeypatch.setattr(scenario, "jira_search", lambda jql: list(box["issues"]))
    return box


def file_tickets(box, count, status="Open", first=13, created="2026-11-16T10:00:00.000+0000"):
    def poster(url, body):
        box["issues"] += [ticket(f"OPS-{first + n}", created, status) for n in range(count)]
    return poster


def test_a_second_run_counts_only_its_own_tickets(mock):
    sc = {"name": "t", "steps": [STEP], "expect": [{"jira.issue_count": {"jql": "labels = wave", "count": 12, "within": "20s"}}]}
    rep = play(sc, poster=file_tickets(mock, 12))  # 12 stale + 12 new: 24 in Jira, 12 of this run
    assert rep.ok


def test_stale_tickets_alone_do_not_satisfy_a_count_a_status_or_a_label(mock):
    for exp in ({"jira.issue_count": {"jql": "x", "count": 12, "within": "20s"}},
                {"jira.issue_status": {"jql": "x", "status": "Resolved", "within": "20s"}},
                {"jira.issue_created": {"labels": ["fault-fan"], "within": "20s"}}):
        rep = play({"name": "t", "steps": [STEP], "expect": [exp]})  # nothing is filed
        assert not rep.ok, exp


@pytest.mark.parametrize("filed,passes", [("Open", False), ("Resolved", True)])
def test_a_new_ticket_decides_the_status_check_not_the_stale_resolved_one(mock, filed, passes):
    sc = {"name": "t", "steps": [STEP], "expect": [{"jira.issue_status": {"jql": "x", "status": "Resolved", "within": "20s"}}]}
    assert play(sc, poster=file_tickets(mock, 1, filed)).ok is passes


def test_issue_created_sees_a_new_ticket(mock):
    sc = {"name": "t", "steps": [STEP], "expect": [{"jira.issue_created": {"labels": ["fault-fan"], "within": "20s"}}]}
    assert play(sc, poster=file_tickets(mock, 1)).ok


def test_a_reset_that_restarts_the_keys_does_not_hide_the_new_tickets(mock):
    """After a mock reset the new ticket is OPS-1 again: key and creation time identify a ticket, not the key."""
    def reset_then_file(url, body):
        mock["issues"][:] = [ticket("OPS-1", "2026-11-16T10:00:00.000+0000")]
    sc = {"name": "t", "steps": [STEP], "expect": [{"jira.issue_count": {"jql": "x", "count": 1, "within": "20s"}}]}
    assert play(sc, poster=reset_then_file).ok


def test_the_snapshot_is_taken_per_jira_and_only_for_those_asked(monkeypatch):
    calls = []
    monkeypatch.setattr(scenario, "jira_search", lambda jql: calls.append(("mock", jql)) or [ticket("OPS-1", "a")])
    monkeypatch.setattr(scenario, "real_search", lambda jql: calls.append(("real", jql)) or [ticket("OPS-2", "b")])
    assert scenario.jira_snapshot([{"slurm.stepd": {"node": "cn001"}}]) == {}
    both = scenario.jira_snapshot([{"jira.issue_count": {"jql": "x", "count": 1}}, {"jira.issue_count": {"jql": "x", "count": 1, "target": "real"}}])
    assert both == {"mock": frozenset({("OPS-1", "a")}), "real": frozenset({("OPS-2", "b")})}
    assert scenario.jira_snapshot([{"jira.issue_count": {"jql": "x", "count": 1, "target": "real"}}]) == {"real": frozenset({("OPS-2", "b")})}


def test_the_real_jira_expectation_ignores_its_stale_ticket(monkeypatch):
    stale = [ticket("OPS-9", "old")]
    box = {"real": list(stale)}
    monkeypatch.setattr(scenario, "real_search", lambda jql: list(box["real"]))
    monkeypatch.setattr(scenario, "jira_search", lambda jql: [])
    sc = {"name": "t", "steps": [STEP], "expect": [{"jira.issue_count": {"jql": "labels = stress", "count": 1, "target": "real", "within": "20s"}}]}
    assert not play(sc).ok  # only the stale one is there
    assert play(sc, poster=lambda u, b: box["real"].append(ticket("OPS-10", "new"))).ok


def test_the_page_run_takes_the_same_snapshot(mock):
    """`run(host=False)` (the web page) also leaves out the stale tickets; `jira.*` expectations run there."""
    sc = {"name": "t", "steps": [STEP], "expect": [{"jira.issue_count": {"jql": "x", "count": 2, "within": "20s"}}]}
    clock = Clock()
    rep = scenario.run(sc, host=False, say=lambda m: None, sleep=clock.sleep, clock=clock, poster=file_tickets(mock, 2))
    assert rep.ok


def test_the_mock_is_read_page_by_page_so_stale_tickets_past_the_first_page_are_in_the_snapshot(monkeypatch):
    import io
    import json
    import urllib.parse

    everything = [ticket(f"OPS-{n}", "2026-11-16T09:00:00.000+0000") for n in range(1, 1201)]  # more than one page of stale tickets
    monkeypatch.setattr(scenario, "JIRA_PAGE", 500)
    asked = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(url, timeout):
        start = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["startAt"][0])
        asked.append(start)
        return Reply(json.dumps({"total": len(everything), "issues": everything[start:start + 500]}).encode())

    monkeypatch.setattr(scenario.urllib.request, "urlopen", urlopen)
    assert len(scenario.jira_search("")) == 1200 and asked == [0, 500, 1000]
    snap = scenario.jira_snapshot([{"jira.issue_count": {"jql": "x", "count": 1}}])
    assert ("OPS-1200", "2026-11-16T09:00:00.000+0000") in snap["mock"]


def test_a_jira_that_cannot_be_read_gives_no_tickets(monkeypatch):
    def refuse(url, timeout):
        raise OSError("refused")
    monkeypatch.setattr(scenario.urllib.request, "urlopen", refuse)
    assert scenario.jira_search("x") == []
