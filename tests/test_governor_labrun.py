"""Governor lab runs (issue 554 steps 4 and owner decisions round 3): `lab-run` answering at once (preparing, refused, running),
`lab-stop` (and the forced stop of `down --force`), admission, the supervising thread's pause (L2) and abort (L3) flags, recovery
after a restart (`recovering`, the restore-only child), interference, `status`'s lab block, and the commands refused during a run.
Fakes only: no benchctl child, no Docker."""
import json
import threading

import pytest

from benchctl import lab_slurm
from governor import control
from governor.core import Governor
from governor.labrun import (
    ABORT_FLAG,
    ACTIVE,
    INTERFERENCE,
    NORESTORE_FLAG,
    PAUSE_FLAG,
    RESTARTED,
    LabRun,
    admission_mb,
    proc_start,
)
from governor.ladder import Ladder
from governor.signals import GB, FileSampler

RID = "20261117-120000"


class Proc:
    def __init__(self, polls=2, rc=0, pid=4242):
        self.left, self.rc, self.pid = polls, rc, pid

    def poll(self):
        if self.left > 0:
            self.left -= 1
            return None
        return self.rc


class Env:
    """A workdir with a lab run's files, a settable level, a recorded audit and fake children. Threads are collected, not started."""

    def __init__(self, tmp_path, level=0, admit_ok=True, nodes=4, prep_error=None, polls=2, state="idle"):
        self.wd, self.level, self.audit, self.spawned, self.read = tmp_path, level, [], [], []
        self.admitted, self.threads, self.starts = [], [], {}
        self.admit_ok, self.nodes, self.prep_error, self.polls, self.state = admit_ok, nodes, prep_error, polls, state
        self.lr = LabRun(tmp_path, lambda: self.level, self.audit.append, self.admit, self.reader, self.spawn,
                         interval=0, sleep=lambda s: None, start_thread=self.threads.append, started=lambda pid: self.starts.get(pid))
        (tmp_path / "compose" / "generated" / "lab").mkdir(parents=True, exist_ok=True)

    def admit(self, mb):
        self.admitted.append(mb)
        return {"ok": True} if self.admit_ok else {"ok": False, "error": "refused: 400 MB would leave 0.9 GB (floor 1.0)"}

    def progress(self, **kw):
        folder = self.lr.folder("prod-a", RID)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "progress.json").write_text(json.dumps({"state": self.state, "percent": 0, "submitted": 0, "running": 0, "pending": 0,
                                                          "paused_s": 0.0, "run": RID, "side": None, **kw}))

    def record(self, **kw):
        self.lr.folder("prod-a", RID).mkdir(parents=True, exist_ok=True)
        (self.lr.folder("prod-a", RID) / "run.json").write_text(json.dumps(kw))

    def reader(self, argv):
        self.read.append(argv)
        if self.prep_error:
            raise RuntimeError(self.prep_error)
        self.progress()
        return json.dumps({"run": RID, "lab_nodes": self.nodes})

    def spawn(self, argv):
        self.spawned.append(argv)
        p = Proc(self.polls)
        self.starts[p.pid] = "777"
        return p

    def flag(self, name):
        p = self.wd / "compose" / "generated" / "flags" / name
        return p.read_text().strip() if p.exists() else None

    def launch(self, supervise=False):
        """Run the start thread; without `supervise` the run stays `running` (the supervising loop is not entered)."""
        if not supervise:
            self.lr.supervise = lambda: None
        self.threads.pop(0)()


def test_admission_is_nodes_times_13_plus_250_as_in_lab_slurm():
    assert admission_mb(4) == 4 * 13 + 250 == lab_slurm.admission_mb(4)
    assert admission_mb(24) == lab_slurm.admission_mb(24)


def test_start_answers_at_once_then_prepares_admits_spawns_and_records(tmp_path):
    e = Env(tmp_path)
    (tmp_path / "compose" / "generated" / "flags").mkdir(parents=True)
    (tmp_path / "compose" / "generated" / "flags" / ABORT_FLAG).write_text("left from the last run")
    r = e.lr.start("prod-a", ab=True, target_hours=1.5, seed=7)
    assert r == {"ok": True, "started": True, "state": "preparing", "name": "prod-a"} and not e.read
    assert e.lr.progress()["state"] == "preparing" and e.flag(ABORT_FLAG) is None
    again = e.lr.start("prod-a")
    assert again["ok"] is False and "(preparing) of prod-a is in progress" in again["error"]  # the placeholder holds the slot
    e.launch()
    assert e.read == [["run", "lab", "prod-a", "--prepare", "-o", "json", "--ab", "--target-hours", "1.5", "--seed", "7"]]
    assert e.admitted == [302] and e.spawned == [["run", "lab", "prod-a", "--run", RID, "--execute"]]
    active = json.loads((tmp_path / "compose/generated/lab" / ACTIVE).read_text())
    assert active == {"name": "prod-a", "run": RID, "ab": True, "admit_mb": 302, "pid": 4242, "pid_start": "777"}
    p = e.lr.progress()
    assert p["state"] == "idle" and p["run"] == RID and p["admit_mb"] == 302 and p["active"] is True
    assert any("started (A/B, admitted 302 MB)" in a for a in e.audit)


def test_refusals_are_kept_in_status_until_the_next_start(tmp_path):
    e = Env(tmp_path, level=2)
    assert e.lr.start("prod-a") == {"ok": False, "error": "refused at L2: lab runs start only at L0-L1 (GV-8)"}
    e = Env(tmp_path / "b", prep_error="lab prod-a has no trace.sacct")
    e.lr.start("prod-a")
    e.launch()
    p = e.lr.progress()
    assert p["state"] == "refused" and "has no trace.sacct" in p["reason"] and e.lr.active is None and not e.spawned
    e = Env(tmp_path / "c", admit_ok=False, nodes=12)
    e.lr.start("prod-a")
    e.launch()
    p = e.lr.progress()
    assert p["state"] == "refused" and "a run needs 406 MB: 12 lab nodes x 13 MB + 250 MB" in p["reason"]
    assert not e.lr.folder("prod-a", RID).exists()
    e.prep_error, e.admit_ok = None, True
    e.lr.start("prod-a")
    assert e.lr.progress()["state"] == "preparing"  # a new start clears the refusal


def test_start_refuses_an_unexpected_run_id_and_a_failed_spawn(tmp_path):
    e = Env(tmp_path)
    e.lr.reader = lambda argv: json.dumps({"run": "../x", "lab_nodes": 1})
    e.lr.start("prod-a")
    e.launch()
    assert "unexpected run id" in e.lr.progress()["reason"]
    e = Env(tmp_path / "b")

    def boom(argv):
        raise OSError("no python")
    e.lr.spawn = boom
    e.lr.start("prod-a")
    e.launch()
    assert "could not start the run" in e.lr.progress()["reason"] and e.lr.active is None
    assert not (tmp_path / "b" / "compose/generated/lab" / ACTIVE).exists()


def test_stop_while_preparing_cancels_before_admission(tmp_path):
    e = Env(tmp_path)
    e.lr.start("prod-a")
    assert e.lr.stop("prod-a") == {"ok": True, "stopping": True, "run": None}
    e.launch()
    assert e.lr.progress()["state"] == "refused" and "before it started" in e.lr.progress()["reason"] and not e.admitted


def test_tick_pauses_at_l2_resumes_below_and_aborts_at_l3(tmp_path):
    e = Env(tmp_path)
    e.lr.start("prod-a")
    e.launch()
    e.level = 2
    e.lr.tick()
    assert e.flag(PAUSE_FLAG) == "L2" and e.flag(ABORT_FLAG) is None
    e.level = 1
    e.lr.tick()
    assert e.flag(PAUSE_FLAG) is None
    e.level = 3
    e.lr.tick()
    assert e.flag(PAUSE_FLAG) == "L3" and e.flag(ABORT_FLAG) == "aborted under memory pressure (L3)"
    e.level = 4
    e.lr.tick()
    assert e.flag(ABORT_FLAG) == "aborted under memory pressure (L3)"  # written once
    assert any("paused at L2" in a for a in e.audit) and any("resumed at L1" in a for a in e.audit)
    assert not (tmp_path / "compose" / "generated" / "flags" / "arrivals.paused").exists()  # L1's flag is not the lab's


def test_supervise_ticks_until_the_child_ends_then_clears(tmp_path):
    e = Env(tmp_path, polls=3)
    e.lr.start("prod-a")
    e.level = 0
    e.launch(supervise=True)
    assert e.lr.active is None and e.flag(PAUSE_FLAG) is None
    assert not (tmp_path / "compose/generated/lab" / ACTIVE).exists()
    assert any(f"run {RID} ended idle" in a for a in e.audit)


def test_supervise_writes_the_abort_flag_past_the_longest_run_and_survives_a_flag_failure(tmp_path):
    e = Env(tmp_path, polls=2)
    e.lr.start("prod-a")
    e.launch()
    e.lr._proc = Proc(2)
    t = iter([0.0, 10**9, 10**9, 10**9])
    e.lr.clock = lambda: next(t)
    orig, calls = e.lr.tick, []

    def tick():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("read-only")
        orig()
    e.lr.tick = tick
    del e.lr.supervise  # the class's own loop again
    e.lr.supervise()
    assert any("flag write failed" in a for a in e.audit)
    assert e.flag(ABORT_FLAG) and "longer than" in e.flag(ABORT_FLAG)


def test_stop_writes_the_abort_flag_and_force_skips_the_restore(tmp_path):
    e = Env(tmp_path)
    assert e.lr.stop("prod-a") == {"ok": False, "error": "no run of lab prod-a is in progress"}
    e.lr.start("prod-a")
    e.launch()
    assert e.lr.stop("other")["ok"] is False
    e.level = 4  # stop works at every level
    assert e.lr.stop("prod-a") == {"ok": True, "stopping": True, "run": RID, "force": False}
    assert e.flag(ABORT_FLAG) == "stopped by the operator (benchctl stop lab)" and e.flag(NORESTORE_FLAG) is None
    assert e.lr.stop("prod-a", force=True)["force"] is True
    assert e.flag(NORESTORE_FLAG) == "benchctl down --force" and "restore is skipped" in e.flag(ABORT_FLAG)


# ---------------------------------------------------------------- recovery after a governor restart

def orphan(e, pid=99, pid_start="555"):
    (e.wd / "compose/generated/lab" / ACTIVE).write_text(json.dumps({"name": "prod-a", "run": RID, "ab": True, "admit_mb": 302,
                                                                       "pid": pid, "pid_start": pid_start}))


def test_recover_waits_for_a_live_old_child_and_refuses_meanwhile(tmp_path):
    e = Env(tmp_path)
    assert e.lr.recover() is None
    flags = tmp_path / "compose" / "generated" / "flags"
    flags.mkdir(parents=True)
    (flags / PAUSE_FLAG).write_text("L2")
    orphan(e)
    e.starts[99] = "555"  # the old child is still alive
    e.progress()
    e.record(state="replay_a", ab=True, snapshot={"x": 1})
    assert "recovering" in e.lr.recover()
    assert e.flag(ABORT_FLAG) == RESTARTED and e.flag(PAUSE_FLAG) is None
    p = e.lr.progress()
    assert p["state"] == "recovering" and p["run_state"] == "idle"
    assert e.lr.start("prod-a")["ok"] is False and "being recovered" in e.lr.busy()
    assert e.lr.stop("prod-a")["ok"] is False  # it is restoring: wait
    polls = []

    def sleep(s):
        polls.append(s)
        if len(polls) == 2:  # the old child restores and ends
            e.record(state="aborted", ab=True, snapshot={"x": 1}, restored_after_abort=True)
    e.lr.sleep = sleep
    e.threads.pop(0)()
    assert e.lr.active is None and not e.spawned and any("recovered" in a for a in e.audit)
    assert not (tmp_path / "compose/generated/lab" / ACTIVE).exists()


def test_recover_starts_a_restore_only_child_when_the_old_one_is_gone(tmp_path):
    e = Env(tmp_path, polls=1)
    orphan(e)  # pid 99 not alive (no start time)
    e.record(state="replay_b", ab=True, snapshot={"x": 1})
    e.lr.recover()
    orig = e.spawn

    def spawn(argv):
        e.record(state="aborted", ab=True, snapshot={"x": 1}, restored_after_abort=True)
        return orig(argv)
    e.lr.spawn = spawn
    e.threads.pop(0)()
    assert e.spawned == [["run", "lab", "prod-a", "--run", RID, "--recover"]] and e.lr.active is None
    assert any("restore-only child started" in a for a in e.audit) and any(" recovered" in a for a in e.audit)


def test_recover_reports_a_run_it_could_not_settle_and_ignores_a_bad_record(tmp_path):
    e = Env(tmp_path, polls=1)
    orphan(e)
    e.record(state="aborted", ab=True, snapshot={"x": 1}, restored_after_abort=False)
    e.lr.recover()

    def boom(argv):
        raise OSError("no python")
    e.lr.spawn = boom
    e.threads.pop(0)()
    assert any("could not start the restore-only child" in a for a in e.audit) and any("NOT recovered" in a for a in e.audit)
    (tmp_path / "compose/generated/lab" / ACTIVE).write_text(json.dumps({"name": "../x", "run": RID}))
    assert e.lr.recover() is None and not (tmp_path / "compose/generated/lab" / ACTIVE).exists()


@pytest.mark.parametrize(("rec", "done"), [({"state": "done"}, True), ({"state": "aborted", "ab": False}, True),
                                           ({"state": "aborted", "ab": True, "snapshot": None}, True),
                                           ({"state": "aborted", "ab": True, "snapshot": {}, "restored_after_abort": True}, True),
                                           ({"state": "aborted", "ab": True, "snapshot": {}, "restore_pending": True}, False),
                                           ({"state": "aborted", "ab": True, "snapshot": {}}, False), ({"state": "replay_a"}, False), ({}, False)])
def test_settled(tmp_path, rec, done):
    e = Env(tmp_path)
    e.record(**rec)
    assert e.lr.settled("prod-a", RID) is done


def test_proc_start_reads_the_start_time_and_none_for_a_gone_process(tmp_path):
    import os
    assert proc_start(os.getpid())
    (tmp_path / "7").mkdir()
    (tmp_path / "7" / "stat").write_text("7 (a b) S " + " ".join(str(i) for i in range(30)))
    assert proc_start(7, tmp_path) == "18"  # field 22: the 20th after the state
    assert proc_start(8, tmp_path) is None and proc_start("x", tmp_path) is None


# ---------------------------------------------------------------- interference

def test_interference_is_recorded_only_during_a_replay(tmp_path):
    e = Env(tmp_path)
    assert e.lr.interfere("node-state", "cn001") is False
    e.lr.start("prod-a")
    e.launch()
    assert e.lr.interfere("node-state", "cn001") is False  # not replaying yet
    e.progress(state="replay_a", side="a")
    e.state = "replay_a"
    assert e.lr.interfere("node-state", "cn001") is True
    line = json.loads((e.lr.folder("prod-a", RID) / INTERFERENCE).read_text().splitlines()[0])
    assert line["action"] == "node-state" and line["target"] == "cn001" and line["side"] == "a"
    assert any("is not comparable" in a for a in e.audit)


# ---------------------------------------------------------------- through the control socket and the governor

def gov(tmp_path, level_avail=4 * GB, reader=None, lab_status=None, dry_run=False, labrun=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"mem_available": level_avail, "psi_some": 0, "psi_full": 0}))
    g = Governor(FileSampler(f), Ladder(), [], lambda a: None, dry_run, lab_exists=lambda n: n == "prod-a", lab_status=lab_status,
                 reader=reader, effector=lambda a: 0)
    g.step(0)
    e = None
    if labrun:
        e = Env(tmp_path / "wd")
        e.lr.level, e.lr.admit = (lambda: g.ladder.level), g.admit_locked
        g.labrun = e.lr
    return g, e, f


def d(g, req):
    return control.dispatch(json.dumps(req).encode(), g.handle, g.group_names)


def test_dispatch_checks_lab_run_arguments():
    g = Governor(FileSampler(__import__("pathlib").Path("/nonexistent")), Ladder(), [], lambda a: None, True)
    bad = [({"cmd": "lab-run"}, "needs name"), ({"cmd": "lab-run", "name": "../x"}, "needs name"),
           ({"cmd": "lab-run", "name": "prod-a", "ab": "yes"}, "ab must be"), ({"cmd": "lab-run", "name": "prod-a", "target_hours": 7}, "target_hours"),
           ({"cmd": "lab-run", "name": "prod-a", "target_hours": True}, "target_hours"), ({"cmd": "lab-run", "name": "prod-a", "seed": -1}, "seed"),
           ({"cmd": "lab-run", "name": "prod-a", "seed": 1.5}, "seed"), ({"cmd": "lab-stop", "name": 3}, "lab-stop needs name"),
           ({"cmd": "lab-stop", "name": "prod-a", "force": "yes"}, "force must be")]
    for req, why in bad:
        r = d(g, req)
        assert r["ok"] is False and why in r["error"], (req, r)
    assert control.lab_run_error({"cmd": "lab-run", "name": "prod-a", "ab": True, "target_hours": 0.5, "seed": 0}) is None
    assert control.lab_run_error({"cmd": "lab-stop", "name": "prod-a", "force": True}) is None


def test_governor_lab_run_and_stop_and_status(tmp_path):
    g, e, _ = gov(tmp_path, lab_status=lambda: {"name": "prod-a", "side": "baseline", "time_scale": 1.0, "state": "applied"})
    assert "no lab other on disk" in d(g, {"cmd": "lab-run", "name": "other"})["error"]
    r = d(g, {"cmd": "lab-run", "name": "prod-a", "ab": True})
    assert r["ok"] and r["state"] == "preparing"
    assert g.handle("status", {})["lab"]["state"] == "preparing"
    e.launch()
    assert any("admit 302 MB: ok" in a for a in g.audit)
    lab = g.handle("status", {})["lab"]
    assert lab["name"] == "prod-a" and lab["side"] == "baseline" and lab["state"] == "idle" and lab["run"] == RID
    assert {"percent", "submitted", "running", "pending", "paused_s", "replay_side"} <= set(lab)
    for cmd, extra in (("scale", {"group": "compute", "n": 2}), ("lab-apply", {"name": "prod-a", "side": "candidate"}),
                       ("lab-restore", {"name": "prod-a"}), ("lab-validate", {"name": "prod-a"}), ("set-profile", {"profile": "std"}),
                       ("admit", {"budget_mb": 100}), ("slurm-step", {"name": "cancel-jobs"})):
        r = d(g, {"cmd": cmd, **extra})
        assert r["ok"] is False and "is in progress" in r["error"] and "benchctl stop lab prod-a" in r["error"], cmd
    assert d(g, {"cmd": "node-state", "node": "cn001", "state": "drain"})["ok"]  # operator relief stays allowed
    assert d(g, {"cmd": "lab-stop", "name": "prod-a", "force": True})["force"] is True


def test_governor_records_node_actions_during_a_replay(tmp_path):
    g, e, _ = gov(tmp_path)
    d(g, {"cmd": "lab-run", "name": "prod-a"})
    e.launch()
    e.state = "replay_b"
    e.progress(state="replay_b", side="b")
    d(g, {"cmd": "node-state", "node": "cn002", "state": "down"})
    d(g, {"cmd": "freeze", "target": "workbench-jira-mock-1"})  # not a node: not recorded
    d(g, {"cmd": "slurm-step", "name": "slurmd-restart", "node": "cn001"})
    lines = [json.loads(x) for x in (e.lr.folder("prod-a", RID) / INTERFERENCE).read_text().splitlines()]
    assert [(x["action"], x["target"]) for x in lines] == [("node-state", "cn002"), ("slurm-step slurmd-restart", "cn001")]


def test_governor_refuses_during_recovery_without_the_stop_hint(tmp_path):
    g, e, _ = gov(tmp_path)
    orphan(e)
    e.lr.recover()
    r = d(g, {"cmd": "scale", "group": "compute", "n": 2})
    assert "being recovered" in r["error"] and "benchctl stop lab" not in r["error"]


def test_governor_lab_status_without_a_marker_and_without_a_run(tmp_path):
    g, e, _ = gov(tmp_path)
    assert g.handle("status", {})["lab"] is None
    d(g, {"cmd": "lab-run", "name": "prod-a"})
    e.launch()
    lab = g.handle("status", {})["lab"]
    assert lab["name"] == "prod-a" and lab["side"] is None and lab["state"] == "idle"


def test_governor_lab_run_refusals(tmp_path):
    g, _, _ = gov(tmp_path, labrun=False)
    assert "no lab runs without --workdir" in d(g, {"cmd": "lab-run", "name": "prod-a"})["error"]
    g, _, _ = gov(tmp_path / "dry", dry_run=True)
    assert d(g, {"cmd": "lab-run", "name": "prod-a"}) == {"ok": True, "dry_run": True}
    g, _, _ = gov(tmp_path / "busy")
    g.change = {"job": 1, "state": "running"}
    assert "a change is already running" in d(g, {"cmd": "lab-run", "name": "prod-a"})["error"]
    g, _, _ = gov(tmp_path / "low", level_avail=int(0.3 * GB))
    assert d(g, {"cmd": "lab-run", "name": "prod-a"})["error"].startswith("refused")


def test_lab_apply_asks_admission_from_the_labs_plan(tmp_path):
    seen = []

    def reader(argv):
        seen.append(argv)
        return json.dumps({"scale": {"lab_nodes": 6}})
    g, _, _ = gov(tmp_path, reader=reader, labrun=False)
    r = d(g, {"cmd": "lab-apply", "name": "prod-a", "side": "baseline"})
    assert r["ok"] and seen == [["get", "lab", "prod-a", "-o", "json"]] and any("admit 328 MB: ok" in a for a in g.audit)
    g.admit_floor = 10 * GB
    r = d(g, {"cmd": "lab-apply", "name": "prod-a", "side": "baseline"})
    assert r["ok"] is False and r["error"].startswith("refused: 328 MB would leave")
    g, _, _ = gov(tmp_path / "bad", reader=lambda argv: "not json", labrun=False)
    assert "could not read the lab's plan" in d(g, {"cmd": "lab-apply", "name": "prod-a", "side": "baseline"})["error"]


@pytest.mark.parametrize("cmd", ["lab-run", "lab-stop"])
def test_lab_run_verbs_are_allowed(cmd):
    assert cmd in control.ALLOWED


def test_supervise_runs_in_a_thread_by_default(tmp_path):
    done = threading.Event()
    lr = LabRun(tmp_path, lambda: 0, lambda m: None, lambda mb: {"ok": True}, lambda a: "", lambda a: None)
    lr.start_thread(done.set)
    assert done.wait(2)
