"""A scenario attached to a config lab run (`benchctl run lab NAME --scenario S`, issue 554, ADR 0050 addendum): frozen once, node
selectors on the lab nodes, only steps that can act on them, one replay clock that stops at L2, heal after each side and after an
abort, the run's own steps not counted as interference, expectations reported and never the verdict. Fakes only: no Docker."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, lab, lab_report, lab_run, lab_scenario, lab_steps, scenario, selectors
from benchctl.lab import LabError
from governor import control

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
HOSTS = {"cpu001", "cpu002", "cpu003", "cpu004", "gpu001", "gpu002"}


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "lab"
    lab.import_lab("prod-a", FIX, root=r)
    return r


@pytest.fixture
def scen(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir()
    return d


def write_sc(d: Path, name: str, doc: dict) -> str:
    (d / f"{name}.yaml").write_text(yaml.safe_dump(doc))
    return name


GOOD = {"name": "lab-t", "ref": "SC-99", "description": "d",
        "steps": [{"at": "10m", "target": "slurm", "do": {"drain": ["first cpu node"], "reason": "t"}},
                  {"at": "20m", "target": "slurm", "do": {"power_off": ["first gpu node"]}},
                  {"at": "40m", "target": "slurm", "do": {"power_on": ["first gpu node"], "slurmd_stop": ["second cpu node"]}},
                  {"at": "45m", "target": "slurm", "do": {"resume": ["first cpu node", "first gpu node"], "slurmd_start": ["second cpu node"]}}],
        "expect": [{"slurm.node_state": {"node": "first cpu node", "state": "drain", "after": "10m", "within": "5m"}},
                   {"slurm.stepd": {"node": "first gpu node", "alive": False, "within": "1m"}}]}


# ---------------------------------------------------------------- selectors and the lab's nodes

def test_selectors_pick_from_given_pools_and_name_the_lab_when_short():
    pools = {"cpu": ["a001", "a002"], "gpu": ["g001"]}
    assert selectors.resolve(["first cpu node", "last 2 cpu nodes"], SIM, pools) == ["a001", "a001", "a002"]
    assert selectors.resolve("drain {first gpu node}", SIM, pools) == "drain g001"
    for bad, msg in (("third cpu node", "the lab has 2"), ("any 3 cpu nodes", "the lab has only 2"), ("first k8s cpu node", "the lab has 0")):
        with pytest.raises(selectors.SelectorError, match=msg):
            selectors.resolve(bad, SIM, pools)
    with pytest.raises(selectors.SelectorError, match="no k8s gpu nodes in the lab"):
        selectors.resolve("all k8s gpu nodes", SIM, pools)
    with pytest.raises(selectors.SelectorError, match="sim.yaml has only"):
        selectors.resolve("any 999 cpu nodes", SIM)  # without pools: sim.yaml, as before


def test_lab_nodes_are_the_apply_plans_nodes_cpu_and_gpu_by_gres(root):
    nodes = lab_scenario.lab_node_list("prod-a", SIM, root)
    assert [n["service"] for n in nodes] == [f"lab-node-00{i}" for i in range(1, 7)]
    assert lab_scenario.pools(nodes) == {"cpu": ["cpu001", "cpu002", "cpu003", "cpu004"], "gpu": ["gpu001", "gpu002"]}


def test_a_lab_without_node_lines_is_refused(root, monkeypatch):
    monkeypatch.setattr(lab, "load_baseline", lambda name, r=None: {"slurm": None})
    with pytest.raises(LabError, match="no node lines"):
        lab_scenario.lab_node_list("prod-a", SIM, root)


# ---------------------------------------------------------------- freeze

def test_freeze_resolves_to_lab_nodes_and_keeps_slurm_names_for_scontrol_and_service_names_for_docker(root, scen):
    doc = lab_scenario.freeze("prod-a", write_sc(scen, "lab-t", GOOD), SIM, 2.0, 7200, root, scen)
    assert doc["name"] == "lab-t" and doc["file"] == "lab-t" and doc["ref"] == "SC-99" and doc["factor"] == 2.0
    steps = doc["steps"]
    assert steps[0] == {"at": 600.0, "target": "slurm", "do": {"drain": ["cpu001"], "reason": "t"}}
    assert steps[1]["do"] == {"power_off": ["lab-node-005"]}  # docker pause of the container
    assert steps[2]["do"] == {"power_on": ["lab-node-005"], "slurmd_stop": ["lab-node-002"]}
    assert steps[3]["do"] == {"resume": ["cpu001", "gpu001"], "slurmd_start": ["lab-node-002"]}
    assert doc["expect"][0]["slurm.node_state"]["node"] == "cpu001" and doc["expect"][1]["slurm.stepd"]["node"] == "lab-node-005"
    assert doc["heal"] == {"resume": ["cpu001", "cpu002", "gpu001"], "unpause": ["lab-node-005"], "release": ["lab-node-002"]}
    assert doc["last_lab_s"] == 1350.0 and doc["hosts"]["lab-node-005"] == "gpu001"


def test_the_shipped_lab_scenario_plays_in_a_lab_run(root):
    run = lab_run.Run.create("prod-a", SIM, root, ab=True)
    doc = lab_scenario.freeze("prod-a", "lab-node-loss", SIM, run.r["factor"], run.r["length_s"], root)
    assert [s["at"] for s in doc["steps"]] == [600.0, 1200.0, 2400.0, 2700.0] and doc["heal"]["unpause"] == ["lab-node-002"]
    assert not scenario.validate(scenario.load("lab-node-loss", sim=SIM), SIM)  # and it plays on the simulated cluster too


@pytest.mark.parametrize("bad,msg", [
    ("../x", "must be a scenario name"), ("Drain", "must be a scenario name"), ("nope", "no scenario nope in scenarios/")])
def test_freeze_refuses_a_bad_or_missing_name(root, scen, bad, msg):
    with pytest.raises(LabError, match=msg):
        lab_scenario.freeze("prod-a", bad, SIM, 1.0, 7200, root, scen)


def test_freeze_refuses_a_selector_the_lab_cannot_satisfy(root, scen):
    name = write_sc(scen, "k", {"name": "k", "steps": [{"at": 0, "target": "slurm", "do": {"drain": ["first k8s gpu node"]}}]})
    with pytest.raises(LabError, match="cannot play in a lab run: k: first k8s gpu node: the lab has 0"):
        lab_scenario.freeze("prod-a", name, SIM, 1.0, 7200, root, scen)


def test_every_problem_is_named_and_long_lists_are_cut(root, scen):
    steps = [{"at": f"{i}s", "target": "bmc-farm", "do": {"nodes": ["first cpu node"], "power": "Off"}} for i in range(8)]
    with pytest.raises(LabError, match=r"run: a lab run plays slurm steps only \(.*step 1: target bmc-farm cannot act on lab nodes.*\(and 3 more\)"):
        lab_scenario.freeze("prod-a", write_sc(scen, "many", {"name": "many", "steps": steps}), SIM, 1.0, 7200, root, scen)


def test_problems():
    p = lab_scenario.problems
    assert p({"steps": [{"at": "1m", "target": "slurm", "do": {"drain": ["cpu001"]}}]}, HOSTS, 1.0, 600) == []
    assert "bad or missing 'at'" in p({"steps": [{"target": "slurm", "do": {}}]}, HOSTS, 1.0, 600)[0]
    assert p({"steps": [{"at": 0, "target": "jira", "do": {}}]}, HOSTS, 1.0, 600) == [
        "a lab run plays slurm steps only (the BMC farm, BCM, GPU exporter and the other parts know only the simulated nodes)",
        "step 1: target jira cannot act on lab nodes"]
    assert "cancel, submit cannot play" in p({"steps": [{"at": 0, "target": "slurm", "do": {"submit": {}, "cancel": True}}]}, HOSTS, 1.0, 600)[0]
    assert "cn001 is not a lab node" in p({"steps": [{"at": 0, "target": "slurm", "do": {"drain": ["cn001"]}}]}, HOSTS, 1.0, 600)[0]
    late = p({"steps": [{"at": "2h", "target": "slurm", "do": {"drain": ["cpu001"]}}]}, HOSTS, 2.0, 600)
    assert late == ["the last step plays at 3600 s of lab time, after the side ends (600 s at time factor x2)"]


def test_steps_on_one_node_squashed_under_a_second_by_the_factor_are_refused():
    sc = {"steps": [{"at": "10s", "target": "slurm", "do": {"drain": ["cpu001"]}}, {"at": "40s", "target": "slurm", "do": {"resume": ["cpu001"]}},
                    {"at": "10s", "target": "slurm", "do": {"down": ["cpu002"]}}]}
    assert lab_scenario.problems(sc, HOSTS, 10.0, 600) == []  # 3 s apart in lab time
    bad = lab_scenario.problems(sc, HOSTS, 100.0, 600)
    assert bad == ["steps on cpu001 at 10 s and 40 s land 0.30 s apart at time factor x100; a lab run needs at least 100 s of scenario time between them"]
    same = {"steps": [{"at": "10s", "target": "slurm", "do": {"drain": ["cpu001"]}}, {"at": "10s", "target": "slurm", "do": {"power_off": ["cpu001"]}}]}
    assert lab_scenario.problems(same, HOSTS, 100.0, 600) == []  # meant together, not squashed
    ctld = {"steps": [{"at": "1s", "target": "slurm", "do": {"slurmctld_restart": True}}, {"at": "2s", "target": "slurm", "do": {"slurmctld_restart": True}}]}
    assert "steps on slurmctld" in lab_scenario.problems(ctld, HOSTS, 10.0, 600)[0]


def test_expectations_that_cannot_be_checked_on_lab_nodes_are_refused():
    p = lab_scenario.problems
    assert "k8s.node_ready cannot be checked on lab nodes" in p({"expect": [{"k8s.node_ready": {"node": "x"}}]}, HOSTS, 1.0, 600)[0]
    assert "cn001 is not a lab node" in p({"expect": [{"slurm.job_state": {"node": "cn001", "state": "RUNNING"}}]}, HOSTS, 1.0, 600)[0]
    assert "not a duration" in p({"expect": [{"slurm.node_state": {"node": "cpu001", "state": "drain", "within": "soon"}}]}, HOSTS, 1.0, 600)[0]
    assert "one assertion" in p({"expect": ["x"]}, HOSTS, 1.0, 600)[0]


def test_write_freezes_read_only_and_load_refuses_a_changed_copy(tmp_path):
    doc = {"name": "s", "file": "s", "ref": None, "factor": 1.0, "steps": [{"at": 0}], "expect": [], "last_lab_s": 0.0}
    summ = lab_scenario.write(tmp_path, doc)
    assert summ["name"] == "s" and summ["steps"] == 1 and summ["expects"] == 0 and len(summ["sha256"]) == 64
    assert (tmp_path / lab_scenario.FILE).stat().st_mode & 0o222 == 0
    assert lab_scenario.load(tmp_path, summ["sha256"]) == doc
    with pytest.raises(LabError, match="changed after it was frozen"):
        lab_scenario.load(tmp_path, "0" * 64)


# ---------------------------------------------------------------- heal

def test_heal_unpauses_releases_and_resumes_and_counts_only_real_failures():
    doc = {"heal": {"resume": ["cpu001"], "unpause": ["lab-node-005"], "release": ["lab-node-002"]}}
    assert lab_scenario.heal_commands(doc) == [
        ["docker", "unpause", "workbench-lab-node-005-1"],
        ["docker", "exec", "workbench-lab-node-002-1", "rm", "-f", scenario.HOLD],
        ["docker", "exec", scenario.SLURMCTLD, "scontrol", "update", "nodename=cpu001", "state=resume"]]
    assert lab_scenario.heal(doc, lambda c: SimpleNamespace(returncode=1)) == {"commands": 3, "failed": 1}  # only the rm counts

    def boom(c):
        raise OSError("docker gone")
    assert lab_scenario.heal(doc, boom) == {"commands": 3, "failed": 1}
    assert lab_scenario.heal({}, boom) == {"commands": 0, "failed": 0}


# ---------------------------------------------------------------- the player

class FakeTime:
    def __init__(self):
        self.t = 0.0

    def mono(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.01)


def doc_of(steps, expect=(), factor=10.0):
    return {"name": "t", "factor": factor, "steps": steps, "expect": list(expect), "hosts": {"lab-node-001": "cpu001"}}


def test_player_plays_steps_at_production_time_over_the_factor_on_the_replay_clock_tagged_lab_run(monkeypatch):
    ft, calls, recorded = FakeTime(), [], []
    monkeypatch.setattr(scenario.lab_interference, "record", lambda action, target, by, *a, **k: recorded.append(by))
    runner = lambda c: calls.append((round(ft.t, 2), c)) or SimpleNamespace(returncode=0, stdout="", stderr="")
    d = doc_of([{"at": 0, "target": "slurm", "do": {"drain": ["cpu001"], "reason": "x"}},
                {"at": 30, "target": "slurm", "do": {"power_off": ["lab-node-001"]}}])
    pl = lab_scenario.Player(d, lambda: False, lambda: False, runner, ft.mono, ft.sleep, start_thread=lambda fn: fn())
    pl.sync(0.0)
    res = pl.finish()
    assert calls[0][0] == 0.0 and "nodename=cpu001" in calls[0][1] and calls[1][1] == ["docker", "pause", "workbench-lab-node-001-1"]
    assert 3.0 <= calls[1][0] < 3.6  # 30 s of production time at factor 10
    assert recorded == ["lab-run", "lab-run"]
    assert res["started"] and res["planned_steps"] == 2 and [s["status"] for s in res["steps"]] == ["ok", "ok"]
    assert "cpu001" in res["steps"][1]["label"] and "lab-node-001" not in res["steps"][1]["label"]  # the report names the Slurm node


def test_a_pause_stops_the_scenario_clock_and_moves_later_steps_by_exactly_the_pause(monkeypatch):
    monkeypatch.setattr(scenario.lab_interference, "record", lambda *a, **k: False)
    ft, calls = FakeTime(), []
    paused = lambda: 1.0 <= ft.t < 11.0
    runner = lambda c: calls.append(round(ft.t, 1)) or SimpleNamespace(returncode=0, stdout="", stderr="")
    d = doc_of([{"at": 30, "target": "slurm", "do": {"drain": ["cpu001"]}}])
    pl = lab_scenario.Player(d, paused, lambda: False, runner, ft.mono, ft.sleep, start_thread=lambda fn: fn())
    pl.sync(0.0)
    pl.finish()
    assert len(calls) == 1 and 12.4 <= calls[0] <= 13.6  # lab time 3 s plus the 10 s pause (to within one 0.5 s slice)


def test_while_paused_the_player_neither_sleeps_out_nor_polls_and_the_clock_holds():
    ft = FakeTime()
    state = {"paused": True}
    pl = lab_scenario.Player(doc_of([]), lambda: state["paused"], lambda: False, mono=ft.mono, real_sleep=ft.sleep, start_thread=lambda fn: None)
    assert pl.now() == 0.0  # not started: the clock waits for the driver
    pl.sync(5.0)
    ft.t += 3
    assert pl.now() == 5.0  # paused: no time passes
    state["paused"] = False
    ft.t += 2
    assert pl.now() == 7.0
    calls = []
    pl.real_sleep = lambda s: calls.append(s) or (state.update(paused=len(calls) < 4))
    state["paused"] = True
    pl.sleep(3)
    assert calls == [0.5, 0.5, 0.5, 0.5]  # blocks in slices until the pause ends
    pl.stop.set()
    state["paused"] = True
    calls.clear()
    pl.sleep(0.2)
    assert calls == [0.2]  # a stopped player does not wait out a pause


def test_finish_reports_unreached_expectations_and_an_engine_error():
    ft = FakeTime()
    exp = [{"slurm.node_state": {"node": "lab-node-001", "state": "drain", "within": "5m"}}]

    def play(doc, **kw):
        assert kw["speed"] == 10.0 and kw["dilation"] == 1.0 and kw["host"] is True and kw["by"] == "lab-run"
        raise RuntimeError("engine broke")
    pl = lab_scenario.Player(doc_of([], exp), lambda: False, lambda: False, mono=ft.mono, real_sleep=ft.sleep, play=play,
                             start_thread=lambda fn: fn())
    pl.sync(0)
    res = pl.finish()
    assert res["error"] == "RuntimeError: engine broke"
    assert res["expects"] == [{"label": 'slurm.node_state {"node": "cpu001", "state": "drain"}', "status": "not reached", "detail": "the side ended first"}]


def test_finish_keeps_the_engines_expectation_outcomes():
    def play(doc, **kw):
        return scenario.Report("t", expects=[scenario.Outcome('slurm.stepd {"node": "lab-node-001"}', "failed", "late")])
    pl = lab_scenario.Player(doc_of([], [{"slurm.stepd": {"node": "lab-node-001"}}]), lambda: False, lambda: False, play=play, start_thread=lambda fn: fn())
    pl.sync(0)
    assert pl.finish()["expects"] == [{"label": 'slurm.stepd {"node": "cpu001"}', "status": "failed", "detail": "late"}]


def test_player_runs_in_a_thread_by_default_and_the_abort_flag_stops_it():
    ab = threading.Event()
    seen = []

    def play(doc, **kw):
        seen.append(kw["should_stop"]())
        ab.set()
        seen.append(kw["should_stop"]())
    pl = lab_scenario.Player(doc_of([]), lambda: False, ab.is_set, play=play)
    pl.sync(0)
    pl.sync(1)  # a second progress line does not start a second thread
    pl.finish(timeout=5)
    assert seen == [False, True]


def test_expect_table_marks_sides_that_differ():
    t = lab_scenario.expect_table({"a": {"expects": [{"label": "x", "status": "ok"}]}, "b": {"expects": [{"label": "x", "status": "failed"}]}})
    assert t == [{"label": "x", "a": "ok", "b": "failed", "differs": True}]
    assert lab_scenario.expect_table({"b": None}) == []


# ---------------------------------------------------------------- the run's own steps are not interference

def test_the_runs_own_scenario_lines_do_not_make_it_not_comparable(root):
    run = lab_run.Run.create("prod-a", SIM, root, ab=True)
    (run.folder / lab_run.INTERFERENCE).write_text(json.dumps({"at": "t", "action": "scenario step drain", "target": "slurm", "side": "a", "by": "lab-run"}) + "\n")
    assert run.interference() is None and run.comparable()
    with (run.folder / lab_run.INTERFERENCE).open("a") as f:
        f.write(json.dumps({"at": "t2", "action": "node-state", "target": "cpu001", "side": "a", "by": "governor"}) + "\n")
    assert run.interference()["action"] == "node-state" and not run.comparable()


def test_the_engine_tags_its_steps_with_by(monkeypatch):
    seen = []
    monkeypatch.setattr(scenario.lab_interference, "record", lambda action, target, by, *a, **k: seen.append(by))
    ok = lambda c: SimpleNamespace(returncode=0, stdout="", stderr="")
    scenario.execute(scenario.Step(0, "slurm", {"drain": ["cpu001"]}), runner=ok)
    scenario.execute(scenario.Step(0, "slurm", {"drain": ["cpu001"]}), runner=ok, by="lab-run")
    assert seen == ["scenario", "lab-run"]


# ---------------------------------------------------------------- the run's steps

class Proc:
    def __init__(self, lines):
        self.stdout = iter(lines)

    def wait(self):
        return 0


class FakePlayer:
    made: ClassVar[list] = []

    def __init__(self, doc, paused, aborted):
        self.doc, self.paused, self.aborted, self.synced = doc, paused, aborted, []
        FakePlayer.made.append(self)

    def sync(self, s):
        self.synced.append(s)

    def finish(self):
        return {"started": True, "steps": [{"label": "slurm x", "status": "ok"}], "planned_steps": 1, "expects": [], "error": None}


def prepared(root, scen, ab=True):
    p = lab_steps.prepare("prod-a", SIM, ab, None, 1, root, scenario=write_sc(scen, "lab-t", GOOD), scenarios=scen)
    return lab_run.Run.load("prod-a", p["run"], root), p


def test_prepare_freezes_the_scenario_beside_the_jobs_and_a_bad_one_leaves_no_run(root, scen):
    run, p = prepared(root, scen)
    assert p["scenario"] == "lab-t" and run.r["scenario"]["file"] == "lab-t" and run.r["scenario"]["steps"] == 4
    assert lab_scenario.load(run.folder, run.r["scenario"]["sha256"])["steps"][0]["do"]["drain"] == ["cpu001"]
    before = sorted(lab_run.reports_dir("prod-a", root).glob("*"))
    write_sc(scen, "bad", {"name": "bad", "steps": [{"at": 0, "target": "jira", "do": {}}]})
    with pytest.raises(LabError, match="target jira cannot act"):
        lab_steps.prepare("prod-a", SIM, True, None, 1, root, scenario="bad", scenarios=scen)
    assert sorted(lab_run.reports_dir("prod-a", root).glob("*")) == before
    assert lab_steps.prepare("prod-a", SIM, False, None, 1, root)["scenario"] is None


def test_replay_feeds_the_driver_clock_to_the_player_and_keeps_its_result(root, scen, tmp_path):
    run, _ = prepared(root, scen)
    run.move("snapshot")
    run.move("apply_baseline")
    run.move("replay_a")
    lines = ['@progress {"submitted": 1, "running": 0, "pending": 0, "replay_s": 0}', '@progress {"submitted": 2, "replay_s": 5.5}',
             '@done {"reason": null}']
    FakePlayer.made.clear()
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", popen=lambda argv: Proc(lines), player=FakePlayer)
    assert s.replay("a", run) is None
    pl = FakePlayer.made[0]
    assert pl.synced == [0.0, 5.5] and pl.doc["name"] == "lab-t" and pl.paused() is False and pl.aborted() is False
    assert run.r["scenario_result"]["a"]["steps"][0]["status"] == "ok"
    assert lab_run.Run.load("prod-a", run.r["run"], root).r["scenario_result"]["a"]["planned_steps"] == 1


def test_a_run_without_scenario_has_no_player_and_no_heal(root, tmp_path):
    run = lab_run.Run.create("prod-a", SIM, root, ab=False)
    run.move("apply_candidate")
    run.move("replay_b")
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", popen=lambda argv: Proc(['@done {"reason": null}']),
                           player=lambda *a: pytest.fail("no player without a scenario"))
    assert s.replay("b", run) is None and "scenario_result" not in run.r and s.heal() is None


class State:
    def __init__(self):
        self.calls = []

    def collect(self, side, run):
        self.calls.append("collect")
        return {"sacct.txt": "JobName\n"}

    def restore(self):
        self.calls.append("restore")
        return {"spool_match": True}


def test_collect_heals_first_at_calm_levels_and_leaves_it_to_later_under_a_flag(root, scen, tmp_path):
    run, _ = prepared(root, scen)
    cmds, st = [], State()
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", state=st, heal_runner=lambda c: cmds.append(c) or SimpleNamespace(returncode=0))
    s.collect("a", run)
    assert len(cmds) == 5 and cmds[0][:2] == ["docker", "unpause"] and run.r["scenario_heal"]["a"] == {"commands": 5, "failed": 0}
    (tmp_path / "gen" / "flags").mkdir(parents=True)
    for flag in (lab_steps.PAUSE_FLAG, lab_steps.ABORT_FLAG):
        (tmp_path / "gen" / "flags" / flag).write_text("x")
        cmds.clear()
        s.collect("b", run)
        assert cmds == []  # L2 or an abort: healed after wait_calm instead (GV-8)
        (tmp_path / "gen" / "flags" / flag).unlink()


def test_restore_heals_before_the_snapshot_goes_back(root, scen, tmp_path):
    run, _ = prepared(root, scen)
    order, st = [], State()
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", state=st, heal_runner=lambda c: order.append("heal") or SimpleNamespace(returncode=0))
    st.restore = lambda: order.append("restore") or {"spool_match": True}
    res = s.restore()
    assert order == ["heal"] * 5 + ["restore"] and res["scenario_heal"] == {"commands": 5, "failed": 0}


def test_a_candidate_only_abort_heals_once_calm_and_down_force_skips_it(root, scen, tmp_path):
    run, _ = prepared(root, scen, ab=False)
    cmds, levels = [], [3, 1]
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", state=State(), sleep=lambda x: None,
                           governor=lambda r: {"level": levels.pop(0)}, heal_runner=lambda c: cmds.append(c) or SimpleNamespace(returncode=0))
    run.move("apply_candidate")
    lab_run._abort(run, s, "stopped")
    assert run.state == "aborted" and len(cmds) == 5 and run.r["scenario_heal"]["abort"]["commands"] == 5 and run.r["heal_pending"] is False
    (tmp_path / "gen" / "flags").mkdir(parents=True)
    (tmp_path / "gen" / "flags" / lab_steps.NORESTORE_FLAG).write_text("forced")
    cmds.clear()
    s.heal_after_abort(run)
    assert cmds == []


def test_a_failed_heal_after_abort_is_recorded_not_raised(root, scen, tmp_path):
    run, _ = prepared(root, scen, ab=False)
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", state=State(),
                           governor=lambda r: {"level": 0}, heal_runner=lambda c: SimpleNamespace(returncode=0))
    s.heal = lambda: (_ for _ in ()).throw(RuntimeError("docker gone"))
    s.heal_after_abort(run)
    assert "docker gone" in run.r["scenario_heal_error"]


def test_recover_heals_an_aborted_candidate_only_run(root, scen, tmp_path):
    run, _ = prepared(root, scen, ab=False)
    run.move("apply_candidate")
    run.abort("gone")
    cmds = []
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", state=State(), run_cmd=lambda a: 0,
                           governor=lambda r: {"level": 0}, heal_runner=lambda c: cmds.append(c) or SimpleNamespace(returncode=0))
    s.discard = lambda: None
    lab_steps.recover("prod-a", run.r["run"], None, root, s)
    assert len(cmds) == 5


def test_the_report_lists_the_scenario_and_its_expectations_per_side_without_changing_the_verdict(root, scen, tmp_path):
    run, _ = prepared(root, scen)
    run.r["scenario_result"] = {"a": {"steps": [{"label": "slurm drain", "status": "ok"}], "planned_steps": 4, "expects": [{"label": "e1", "status": "ok"}], "error": None},
                                "b": {"steps": [{"label": "slurm drain", "status": "failed", "detail": "no"}], "planned_steps": 4,
                                      "expects": [{"label": "e1", "status": "failed"}], "error": "boom"}}
    run.save()
    for side in ("a", "b"):
        run.side_dir(side).mkdir(parents=True, exist_ok=True)
        (run.side_dir(side) / "sacct.txt").write_text("JobName|Partition|State\nj0001|cpu|COMPLETED\n")
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", prom_get=lambda u: (_ for _ in ()).throw(OSError("no")))
    s.report(run)
    rep = json.loads((run.folder / "report.json").read_text())
    md = (run.folder / "report.md").read_text()
    assert rep["scenario"]["name"] == "lab-t" and rep["scenario_expects"] == [{"label": "e1", "a": "ok", "b": "failed", "differs": True}]
    assert "## Scenario lab-t" in md and "A: 1 of 4 steps applied" in md and "B: 0 of 4 steps applied; 1 not" in md and "the scenario failed: boom" in md
    assert "| e1 | ok | failed | yes |" in md
    meta = {k: rep[k] for k in ("factor", "comparable")}
    without = lab_report.build({**rep, "scenario": None, "scenario_result": {}, "scenario_expects": []}, None, [], rep["thresholds"], None, [])
    assert meta["comparable"] is True and without["verdict"] is None and "## Scenario" not in lab_report.markdown(without)


# ---------------------------------------------------------------- governor and CLI

def test_control_checks_the_scenario_name():
    ok = {"cmd": "lab-run", "name": "prod-a", "scenario": "lab-node-loss"}
    assert control.lab_run_error(ok) is None
    for bad in ("../etc", "A", "", 3, "x" * 65):
        assert "scenario must be a scenario name" in control.lab_run_error({**ok, "scenario": bad})


def test_governor_refuses_a_scenario_file_that_does_not_exist_and_passes_one_that_does(tmp_path):
    from governor.labrun import LabRun
    threads, read = [], []
    lr = LabRun(tmp_path, lambda: 0, lambda m: None, lambda mb: {"ok": False, "error": "no"}, lambda a: read.append(a) or json.dumps({"run": "20261117-120000", "lab_nodes": 1}),
                lambda a: None, interval=0, sleep=lambda s: None, start_thread=threads.append)
    r = lr.start("prod-a", scenario="nope")
    assert r == {"ok": False, "error": "no scenario nope in scenarios/ (see: benchctl scenario list)"} and not threads
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "lab-node-loss.yaml").write_text("name: lab-node-loss\n")
    assert lr.start("prod-a", scenario="lab-node-loss")["ok"]
    threads.pop(0)()
    assert read[0][-2:] == ["--scenario", "lab-node-loss"]


def test_cli_sends_the_scenario_and_refuses_a_bad_name(monkeypatch):
    sent = []

    def request(req, path=None, timeout=3):
        sent.append(req)
        return {"ok": True, "lab": {"name": "prod-a", "run": "20261117-120000", "state": "replay_a", "ab": True, "admit_mb": 302}}
    monkeypatch.setattr(cli.checks, "governor_request", request)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    r = CliRunner().invoke(cli.app, ["run", "lab", "prod-a", "--ab", "--scenario", "lab-node-loss"])
    assert r.exit_code == 0, r.output
    assert sent[0] == {"cmd": "lab-run", "name": "prod-a", "ab": True, "scenario": "lab-node-loss"}
    r = CliRunner().invoke(cli.app, ["run", "lab", "prod-a", "--scenario", "../x"])
    assert r.exit_code == 1 and "--scenario must be a scenario name" in r.output


def test_cli_prepare_passes_the_scenario(monkeypatch):
    got = {}
    monkeypatch.setattr(lab_steps, "prepare", lambda *a, **k: got.update(k) or {"run": "r", "jobs": 1, "lab_nodes": 1})
    r = CliRunner().invoke(cli.app, ["run", "lab", "prod-a", "--prepare", "--scenario", "lab-node-loss"])
    assert r.exit_code == 0, r.output
    assert got["scenario"] == "lab-node-loss"


def test_named_maps_whole_service_names_only_and_the_anonymized_export_holds_no_production_node(root, scen, tmp_path):
    from benchctl import lab_export
    pl = lab_scenario.Player({"hosts": {"lab-node-002": "cpu002"}}, lambda: False, lambda: False)
    assert pl.named('slurm {"power_off": ["lab-node-002"]}') == 'slurm {"power_off": ["cpu002"]}'
    assert pl.named("Container workbench-lab-node-002-1 is not running") == "Container workbench-lab-node-002-1 is not running"
    run, _ = prepared(root, scen)
    run.r["scenario_result"] = {"a": {"steps": [{"label": pl.named('slurm {"power_off": ["lab-node-002"]}'), "status": "failed",
                                                 "detail": "Container workbench-lab-node-002-1 is not running"}],
                                      "planned_steps": 1, "expects": [{"label": 'slurm.node_state {"node": "cpu001"}', "status": "ok"}], "error": None}}
    run.save()
    for side in ("a", "b"):
        run.side_dir(side).mkdir(parents=True, exist_ok=True)
        (run.side_dir(side) / "sacct.txt").write_text("JobName|Partition|State\nj0001|cpu|COMPLETED\n")
    lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", prom_get=lambda u: (_ for _ in ()).throw(OSError("no"))).report(run)
    doc, md = lab_export.export("prod-a", run.r["run"], anonymize=True, root=root)
    text = json.dumps(doc) + md
    assert not any(h in text for h in HOSTS), [h for h in HOSTS if h in text]
    assert "workbench-lab-node-002-1" in text  # a container name stays as it is
