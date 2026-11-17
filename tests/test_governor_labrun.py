"""Governor lab runs (issue 554 step 4): `lab-run` and `lab-stop`, admission, the supervising thread's pause (L2) and abort (L3)
flags, recovery after a restart, `status`'s lab block, and `lab-apply`'s admission. Fakes only: no benchctl child, no Docker."""
import json
import threading

import pytest

from benchctl import lab_slurm
from governor import control
from governor.core import Governor
from governor.labrun import ABORT_FLAG, ACTIVE, PAUSE_FLAG, LabRun, admission_mb
from governor.ladder import Ladder
from governor.signals import GB, FileSampler

RID = "20261117-120000"


class Proc:
    def __init__(self, polls=2, rc=0):
        self.left, self.rc = polls, rc

    def poll(self):
        if self.left > 0:
            self.left -= 1
            return None
        return self.rc


class Env:
    """A workdir with a lab run's files, a settable level, a recorded audit and fake children."""

    def __init__(self, tmp_path, level=0, admit_ok=True, nodes=4, prep_error=None, polls=2):
        self.wd, self.level, self.audit, self.spawned, self.read = tmp_path, level, [], [], []
        self.admitted = []
        self.admit_ok, self.nodes, self.prep_error, self.polls = admit_ok, nodes, prep_error, polls
        self.threads = []
        self.lr = LabRun(tmp_path, lambda: self.level, self.audit.append, self.admit, self.reader, self.spawn,
                         interval=0, sleep=lambda s: None, start_thread=self.threads.append)
        (tmp_path / "compose" / "generated" / "lab").mkdir(parents=True)

    def admit(self, mb):
        self.admitted.append(mb)
        return {"ok": True} if self.admit_ok else {"ok": False, "error": "refused: 400 MB would leave 0.9 GB (floor 1.0)"}

    def reader(self, argv):
        self.read.append(argv)
        if self.prep_error:
            raise RuntimeError(self.prep_error)
        folder = self.lr.folder("prod-a", RID)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "progress.json").write_text(json.dumps({"state": "idle", "percent": 0, "submitted": 0, "running": 0, "pending": 0,
                                                          "paused_s": 0.0, "run": RID, "side": None}))
        return json.dumps({"run": RID, "lab_nodes": self.nodes})

    def spawn(self, argv):
        self.spawned.append(argv)
        return Proc(self.polls)

    def flag(self, name):
        p = self.wd / "compose" / "generated" / "flags" / name
        return p.read_text().strip() if p.exists() else None


def test_admission_is_nodes_times_13_plus_250_as_in_lab_slurm():
    assert admission_mb(4) == 4 * 13 + 250 == lab_slurm.admission_mb(4)
    assert admission_mb(24) == lab_slurm.admission_mb(24)


def test_start_prepares_admits_spawns_and_records(tmp_path):
    e = Env(tmp_path)
    (tmp_path / "compose" / "generated" / "flags").mkdir(parents=True)
    (tmp_path / "compose" / "generated" / "flags" / ABORT_FLAG).write_text("left from the last run")
    r = e.lr.start("prod-a", ab=True, target_hours=1.5, seed=7)
    assert r == {"ok": True, "started": True, "run": RID, "admit_mb": 302, "ab": True}
    assert e.read == [["run", "lab", "prod-a", "--prepare", "-o", "json", "--ab", "--target-hours", "1.5", "--seed", "7"]]
    assert e.admitted == [302] and e.spawned == [["run", "lab", "prod-a", "--run", RID, "--execute"]]
    assert e.flag(ABORT_FLAG) is None and len(e.threads) == 1
    assert json.loads((tmp_path / "compose/generated/lab" / ACTIVE).read_text())["run"] == RID
    assert any("started (A/B, admitted 302 MB)" in a for a in e.audit)
    again = e.lr.start("prod-a")
    assert again["ok"] is False and "is in progress: one at a time" in again["error"]


def test_start_is_refused_at_l2_when_prepare_fails_and_when_admission_refuses(tmp_path):
    e = Env(tmp_path, level=2)
    r = e.lr.start("prod-a")
    assert r == {"ok": False, "error": "refused at L2: lab runs start only at L0-L1 (GV-8)"} and not e.read
    e = Env(tmp_path / "b", prep_error="lab prod-a has no trace.sacct")
    assert "has no trace.sacct" in e.lr.start("prod-a")["error"] and not e.spawned
    e = Env(tmp_path / "c", admit_ok=False, nodes=12)
    r = e.lr.start("prod-a")
    assert r["ok"] is False and "a run needs 406 MB: 12 lab nodes x 13 MB + 250 MB" in r["error"]
    assert not e.lr.folder("prod-a", RID).exists() and not e.spawned and e.lr.active is None


def test_start_refuses_an_unexpected_run_id_and_a_failed_spawn(tmp_path):
    e = Env(tmp_path)
    e.reader = lambda argv: json.dumps({"run": "../x", "lab_nodes": 1})
    e.lr.reader = e.reader
    assert "unexpected run id" in e.lr.start("prod-a")["error"]
    e = Env(tmp_path / "b")

    def boom(argv):
        raise OSError("no python")
    e.lr.spawn = boom
    r = e.lr.start("prod-a")
    assert r["ok"] is False and "could not start the run" in r["error"] and e.lr.active is None
    assert not (tmp_path / "b" / "compose/generated/lab" / ACTIVE).exists()


def test_only_one_start_at_a_time(tmp_path):
    e = Env(tmp_path)
    e.lr._lock.acquire()
    try:
        assert e.lr.start("prod-a")["error"] == "a lab run is being started: wait for it"
    finally:
        e.lr._lock.release()


def test_tick_pauses_at_l2_resumes_below_and_aborts_at_l3(tmp_path):
    e = Env(tmp_path)
    e.lr.start("prod-a")
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
    e.level = 2
    e.lr.supervise()
    assert e.lr.active is None and e.flag(PAUSE_FLAG) is None
    assert not (tmp_path / "compose/generated/lab" / ACTIVE).exists()
    assert any(f"run {RID} ended idle" in a for a in e.audit)


def test_supervise_writes_the_abort_flag_past_the_longest_run_and_survives_a_flag_failure(tmp_path, monkeypatch):
    e = Env(tmp_path, polls=2)
    e.lr.start("prod-a")
    t = iter([0.0, 10**9, 10**9, 10**9])
    e.lr.clock = lambda: next(t)
    orig = e.lr.tick
    calls = []

    def tick():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("read-only")
        orig()
    e.lr.tick = tick
    flags = []
    e.lr._flag = lambda name, text, f=e.lr._flag: (flags.append((name, text)), f(name, text))
    e.lr.supervise()
    assert any(n == ABORT_FLAG and text and "longer than" in text for n, text in flags)
    assert any("flag write failed" in a for a in e.audit)


def test_stop_writes_the_abort_flag_for_the_running_lab_only(tmp_path):
    e = Env(tmp_path)
    assert e.lr.stop("prod-a") == {"ok": False, "error": "no run of lab prod-a is in progress"}
    e.lr.start("prod-a")
    assert e.lr.stop("other")["ok"] is False
    e.level = 4  # stop works at every level
    assert e.lr.stop("prod-a") == {"ok": True, "stopping": True, "run": RID}
    assert e.flag(ABORT_FLAG) == "stopped by the operator (benchctl stop lab)"


def test_recover_asks_an_orphaned_run_to_abort_and_clears_a_stale_pause(tmp_path):
    e = Env(tmp_path)
    assert e.lr.recover() is None
    flags = tmp_path / "compose" / "generated" / "flags"
    flags.mkdir(parents=True)
    (flags / PAUSE_FLAG).write_text("L2")
    (tmp_path / "compose/generated/lab" / ACTIVE).write_text(json.dumps({"name": "prod-a", "run": RID}))
    assert "lost its supervisor" in e.lr.recover()
    assert e.flag(ABORT_FLAG) == "the governor restarted during the run" and e.flag(PAUSE_FLAG) is None
    assert not (tmp_path / "compose/generated/lab" / ACTIVE).exists()


def test_progress_reads_the_runs_file(tmp_path):
    e = Env(tmp_path)
    assert e.lr.progress() is None
    e.lr.start("prod-a", ab=True)
    p = e.lr.progress()
    assert p["state"] == "idle" and p["run"] == RID and p["active"] is True and p["admit_mb"] == 302
    (e.lr.folder("prod-a", RID) / "progress.json").write_text("{bad")
    assert e.lr.progress()["state"] == "starting"


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
           ({"cmd": "lab-run", "name": "prod-a", "seed": 1.5}, "seed"), ({"cmd": "lab-stop", "name": 3}, "lab-stop needs name")]
    for req, why in bad:
        r = d(g, req)
        assert r["ok"] is False and why in r["error"], (req, r)
    assert control.lab_run_error({"cmd": "lab-run", "name": "prod-a", "ab": True, "target_hours": 0.5, "seed": 0}) is None
    assert control.lab_run_error({"cmd": "lab-stop", "name": "prod-a"}) is None


def test_governor_lab_run_and_stop_and_status(tmp_path):
    g, _e, _ = gov(tmp_path, lab_status=lambda: {"name": "prod-a", "side": "baseline", "time_scale": 1.0, "state": "applied"})
    assert "no lab other on disk" in d(g, {"cmd": "lab-run", "name": "other"})["error"]
    r = d(g, {"cmd": "lab-run", "name": "prod-a", "ab": True})
    assert r["ok"] and r["run"] == RID and r["admit_mb"] == 302
    assert any("admit 302 MB: ok" in a for a in g.audit)
    lab = g.handle("status", {})["lab"]
    assert lab["name"] == "prod-a" and lab["side"] == "baseline" and lab["state"] == "idle" and lab["run"] == RID
    assert {"percent", "submitted", "running", "pending", "paused_s", "replay_side"} <= set(lab)
    for cmd, extra in (("scale", {"group": "compute", "n": 2}), ("lab-apply", {"name": "prod-a", "side": "candidate"}),
                       ("lab-restore", {"name": "prod-a"}), ("lab-validate", {"name": "prod-a"}), ("set-profile", {"profile": "std"})):
        r = d(g, {"cmd": cmd, **extra})
        assert r["ok"] is False and "is in progress" in r["error"] and "benchctl stop lab prod-a" in r["error"], cmd
    assert d(g, {"cmd": "lab-stop", "name": "prod-a"})["stopping"] is True


def test_governor_lab_status_without_a_marker_and_without_a_run(tmp_path):
    g, _e, _ = gov(tmp_path)
    assert g.handle("status", {})["lab"] is None
    d(g, {"cmd": "lab-run", "name": "prod-a"})
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
    g, _e, _ = gov(tmp_path / "low", level_avail=int(0.3 * GB))
    assert d(g, {"cmd": "lab-run", "name": "prod-a"})["error"].startswith("refused")


def test_lab_apply_asks_admission_from_the_labs_plan(tmp_path):
    seen = []

    def reader(argv):
        seen.append(argv)
        return json.dumps({"scale": {"lab_nodes": 6}})
    g, _, _f = gov(tmp_path, reader=reader, labrun=False)
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
