"""Config lab run: state machine, frozen jobs file, user remap, seed, A/B pairing (issue 554 step 1). Synthetic data, no Docker."""
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from benchctl import lab, lab_run
from benchctl.lab import LabError

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
PROD_USERS = {"u1", "u2"}  # the fixture's production users (trace and sacctmgr dump)
T0 = datetime(2026, 1, 5)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "lab"
    lab.import_lab("prod-a", FIX, root=r)
    return r


def big_trace_export(tmp_path, users=("alice.smith", "bob"), n=40, account_of=None):
    """A copy of the fixture with a longer trace of `n` jobs over 10 hours, so dilation and sampling have something to do."""
    d = tmp_path / "export"
    shutil.copytree(FIX, d)
    head = "JobID|User|Account|QOS|Partition|AllocTRES|NNodes|Timelimit|Submit|Start|End|Elapsed|State|ExitCode"
    rows = [head]
    states = ["COMPLETED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "CANCELLED by 0"]
    for i in range(n):
        u = users[i % len(users)]
        acct = (account_of or {}).get(u, "physics" if i % 2 else "ml")
        sub = T0 + timedelta(minutes=i * 15 * 40 // n)  # n jobs spread over the same 10 hours
        start, end = sub + timedelta(seconds=30), sub + timedelta(seconds=3630)
        rows.append(f"{100 + i}|{u}|{acct}|normal|cpu|cpu=4,node=1|1|02:00:00|{sub.isoformat()}|{start.isoformat()}|{end.isoformat()}|"
                    f"01:00:00|{states[i % 5]}|{i % 3}:0")
    (d / "trace.sacct").write_text("\n".join(rows) + "\n")
    return d


# ---------------------------------------------------------------- state machine

def test_ab_path_is_the_issue_order():
    assert lab_run.AB_PATH == ("idle", "snapshot", "apply_baseline", "replay_a", "collect_a", "restore", "apply_candidate",
                               "replay_b", "collect_b", "compare", "report", "done")


def test_candidate_only_path_has_no_snapshot_restore_or_compare():
    for s in ("snapshot", "restore", "compare", "apply_baseline", "replay_a"):
        assert s not in lab_run.CANDIDATE_PATH


@pytest.mark.parametrize("frm,to,ok", [
    ("idle", "snapshot", True), ("snapshot", "apply_baseline", True), ("collect_a", "restore", True),
    ("idle", "replay_a", False), ("replay_a", "restore", False), ("restore", "collect_a", False),
    ("replay_b", "aborted", True), ("idle", "aborted", True), ("done", "aborted", False), ("aborted", "idle", False),
    ("report", "done", True), ("done", "idle", False), ("nowhere", "idle", False)])
def test_legal_transitions(frm, to, ok):
    assert lab_run.legal(lab_run.AB_PATH, frm, to) is ok


def test_candidate_only_starts_with_apply_candidate():
    assert lab_run.legal(lab_run.CANDIDATE_PATH, "idle", "apply_candidate")
    assert not lab_run.legal(lab_run.CANDIDATE_PATH, "idle", "snapshot")


# ---------------------------------------------------------------- jobs file

def test_jobs_file_maps_users_and_keeps_only_allowed_fields(root):
    doc, prod = lab_run.build_jobs("prod-a", SIM, root)
    assert prod == PROD_USERS
    assert doc["users"] == ["lab-u001", "lab-u002"]
    assert {j["user"] for j in doc["jobs"]} <= set(doc["users"])
    allowed = {"key", "user", "account", "qos", "partition", "tres", "nodes", "submit_s", "run_s", "limit_s", "kind", "exit_code", "signal"}
    assert all(set(j) == allowed for j in doc["jobs"])
    assert [j["key"] for j in doc["jobs"]] == [f"j{i:04d}" for i in range(1, len(doc["jobs"]) + 1)]
    assert doc["seed"] == lab_run.DEFAULT_SEED and doc["target_hours"] == 2.0


def test_jobs_file_outcomes(root):
    doc = lab_run.build_jobs("prod-a", SIM, root)[0]
    jobs = {j["kind"]: j for j in doc["jobs"]}
    f = doc["factor"]  # the trace spans 3 h, the target is 2 h: x1.5
    assert f == 1.5 and doc["length_s"] == 7200
    assert jobs["oom"]["exit_code"] == 137  # ADR 0050: OOM exits 137 in the lab
    assert jobs["timeout"]["limit_s"] == 12 * 3600 / f and jobs["completed"]["run_s"] == 20 * 60 / f
    assert jobs["completed"]["submit_s"] == 0.0 and jobs["timeout"]["submit_s"] == 300.0 / f


@pytest.mark.parametrize("state,kind", [("COMPLETED", "completed"), ("CANCELLED by 1234", "cancelled"), ("TIMEOUT", "timeout"),
                                        ("OUT_OF_MEMORY", "oom"), ("PREEMPTED", "preempted"), ("RUNNING", "other"), ("", "other"),
                                        ("FAILED+", "failed")])
def test_kind(state, kind):
    assert lab_run.kind(state) == kind


@pytest.mark.parametrize("text,want", [("1:0", (1, 0)), ("0:9", (0, 9)), ("", (0, 0)), ("x:y", (0, 0)), ("2", (2, 0))])
def test_exit_code(text, want):
    assert lab_run._exit(text) == want


def test_no_production_user_name_in_any_written_file(tmp_path):
    names = ("alice.smith", "bob")
    r = tmp_path / "lab"
    lab.import_lab("prod-b", big_trace_export(tmp_path, names), root=r)
    run = lab_run.Run.create("prod-b", SIM, r, clock=Clock())
    run.move("snapshot")
    for f in run.folder.rglob("*"):
        if f.is_file():
            text = f.read_text()
            for n in names:
                assert n not in text, f"{n} leaked into {f.name}"
    assert lab_run.production_users("prod-b", r) == {"alice.smith", "bob", "u1", "u2"}


def test_a_user_name_used_as_account_is_refused_without_naming_it(tmp_path):
    r = tmp_path / "lab"
    lab.import_lab("prod-c", big_trace_export(tmp_path, ("carol", "dave"), account_of={"carol": "carol"}), root=r)
    with pytest.raises(LabError) as e:
        lab_run.build_jobs("prod-c", SIM, r)
    assert "1 production user name" in str(e.value) and "carol" not in str(e.value)


def test_leaks_counts_tokens_not_substrings():
    assert lab_run.leaks({"tres": "cpu=4,gres/gpu:bob=1"}, {"bob"}) == 1
    assert lab_run.leaks({"account": "bobcat"}, {"bob"}) == 0
    assert lab_run.leaks(["x", {"bob": 1}], {"bob"}) == 1
    assert lab_run.leaks("1|bob|ml", {"bob", "eve"}) == 1


def test_seed_is_recorded_and_changes_the_sample(tmp_path):
    r = tmp_path / "lab"
    lab.import_lab("prod-d", big_trace_export(tmp_path, n=200), root=r)
    sim = {**SIM, "lab": {"max_nodes": 4}}  # four lab nodes of six: two thirds of the jobs are sampled
    a1, _ = lab_run.build_jobs("prod-d", sim, r, seed=7)
    a2, _ = lab_run.build_jobs("prod-d", sim, r, seed=7)
    b, _ = lab_run.build_jobs("prod-d", sim, r, seed=8)
    assert a1 == a2 and a1["seed"] == 7 and b["seed"] == 8
    assert [j["submit_s"] for j in a1["jobs"]] != [j["submit_s"] for j in b["jobs"]]


@pytest.mark.parametrize("seed", [-1, True, 1.5, "3"])
def test_bad_seed_refused(root, seed):
    with pytest.raises(LabError, match="seed"):
        lab_run.build_jobs("prod-a", SIM, root, seed=seed)


def test_target_hours_sets_the_dilation(tmp_path):
    r = tmp_path / "lab"
    lab.import_lab("prod-e", big_trace_export(tmp_path), root=r)  # 40 jobs over about 10.75 hours
    two, _ = lab_run.build_jobs("prod-e", SIM, r)
    four, _ = lab_run.build_jobs("prod-e", SIM, r, target_hours=4)
    assert two["target_hours"] == 2.0 and four["target_hours"] == 4.0
    assert two["factor"] == pytest.approx(2 * four["factor"], rel=1e-3)
    assert two["length_s"] == pytest.approx(7200, abs=2) and four["length_s"] == pytest.approx(14400, abs=2)
    from_sim, _ = lab_run.build_jobs("prod-e", {**SIM, "lab": {"target_hours": 3}}, r)
    assert from_sim["target_hours"] == 3.0


def test_short_trace_runs_its_own_span(root):
    doc, _ = lab_run.build_jobs("prod-a", SIM, root, target_hours=6)
    assert doc["factor"] == 1.0 and doc["length_s"] < 6 * 3600


@pytest.mark.parametrize("hours", [0.1, 7])
def test_target_hours_bounds(root, hours):
    with pytest.raises(LabError, match="target-hours"):
        lab_run.build_jobs("prod-a", SIM, root, target_hours=hours)


def test_lab_without_trace_or_slurm_conf_is_refused(tmp_path):
    d = tmp_path / "export"
    shutil.copytree(FIX, d)
    (d / "trace.sacct").unlink()
    lab.import_lab("notrace", d, root=tmp_path / "lab")
    with pytest.raises(LabError, match="nothing to replay"):
        lab_run.build_jobs("notrace", SIM, tmp_path / "lab")
    (d / "slurm.conf").unlink()
    lab.import_lab("noconf", d, root=tmp_path / "lab")
    with pytest.raises(LabError, match="no slurm.conf"):
        lab_run.build_jobs("noconf", SIM, tmp_path / "lab")


# ---------------------------------------------------------------- the run record

def test_create_freezes_jobs_and_writes_progress(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    jobs = run.folder / "jobs.json"
    assert jobs.stat().st_mode & 0o222 == 0
    assert run.jobs()["jobs"] and run.r["jobs_sha256"] == lab_run.sha256(jobs.read_text())
    p = json.loads((run.folder / "progress.json").read_text())
    for k in ("name", "state", "percent", "submitted", "running", "pending", "paused_s"):  # ADR 0049's status block
        assert k in p
    assert p["state"] == "idle" and p["percent"] == 0 and run.r["sides"] == ["a", "b"]


def test_changed_jobs_file_is_refused(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    f = run.folder / "jobs.json"
    f.chmod(0o644)
    f.write_text(f.read_text().replace("lab-u001", "lab-u009"))
    with pytest.raises(LabError, match="changed after it was frozen"):
        run.jobs()


def test_run_ids_are_unique_and_loadable(root):
    c = Clock()
    a = lab_run.Run.create("prod-a", SIM, root, clock=c)
    b = lab_run.Run.create("prod-a", SIM, root, clock=c)
    assert a.r["run"] != b.r["run"] and b.r["run"].startswith(a.r["run"])
    again = lab_run.Run.load("prod-a", b.r["run"], root)
    assert again.r == b.r and again._prod == PROD_USERS
    with pytest.raises(LabError, match="not a run id"):
        lab_run.Run.load("prod-a", "../x", root)
    with pytest.raises(LabError, match="no run"):
        lab_run.Run.load("prod-a", "20000101-000000", root)


def test_illegal_move_is_refused(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    with pytest.raises(LabError, match="cannot go from idle to replay_a"):
        run.move("replay_a")


def test_candidate_only_run(root):
    run = lab_run.Run.create("prod-a", SIM, root, ab=False, clock=Clock())
    assert run.r["sides"] == ["b"] and run.path == lab_run.CANDIDATE_PATH
    run.move("apply_candidate")
    run.move("replay_b")
    assert run.progress()["side"] == "b"


def test_percent_and_pause(root):
    c = Clock()
    run = lab_run.Run.create("prod-a", SIM, root, clock=c)
    length = run.r["length_s"]
    for s in ("snapshot", "apply_baseline", "replay_a"):
        run.move(s)
    run.report_progress(3, 1, 2, length / 2)
    p = run.progress()
    assert p["percent"] == 25 and (p["submitted"], p["running"], p["pending"]) == (3, 1, 2)
    run.pause()
    run.pause()  # twice is one pause
    c.t += 10
    assert run.progress()["paused"] and run.progress()["paused_s"] == 10.0
    run.resume()
    run.resume()
    c.t += 5
    assert run.progress()["paused_s"] == 10.0 and not run.progress()["paused"]
    for s in ("collect_a", "restore", "apply_candidate"):
        run.move(s)
    assert run.percent() == 50
    run.move("replay_b")
    assert run.progress()["submitted"] == 0  # counts start again for side B
    run.report_progress(1, 0, 0, length)
    assert run.percent() == 99
    for s in ("collect_b", "compare", "report"):
        run.move(s)
    assert run.percent() == 99
    run.move("done")
    assert run.percent() == 100


def test_pause_outside_replay_and_progress_outside_replay_refused(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    with pytest.raises(LabError, match="only a replay can pause"):
        run.pause()
    with pytest.raises(LabError, match="progress outside a replay"):
        run.report_progress(1, 1, 1, 1)


def test_long_pause_makes_the_run_not_comparable_and_move_ends_a_pause(root):
    c = Clock()
    run = lab_run.Run.create("prod-a", SIM, root, clock=c)
    for s in ("snapshot", "apply_baseline", "replay_a"):
        run.move(s)
    run.pause()
    c.t += 0.05 * run.r["length_s"] * 2 + 5  # more than 5 % of both sides' length
    run.move("collect_a")
    assert run.r["paused_since"] is None and not run.comparable()
    assert json.loads((run.folder / "progress.json").read_text())["comparable"] is False


def test_abort_keeps_the_percent_reached(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    for s in ("snapshot", "apply_baseline", "replay_a", "collect_a", "restore"):
        run.move(s)
    run.abort("aborted under memory pressure")
    p = run.progress()
    assert p["state"] == "aborted" and p["reason"] == "aborted under memory pressure" and p["percent"] == 50
    run.abort("again")  # an ended run stays as it ended
    assert run.r["reason"] == "aborted under memory pressure"


def test_collected_export_is_checked_for_production_users(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    with pytest.raises(LabError, match="production user") as e:
        run.record_collected("a", {"sacct.txt": "JobName|User\nj0001|u1\n"})
    assert "u1" not in str(e.value) and not (run.side_dir("a") / "sacct.txt").exists()
    with pytest.raises(LabError, match="bad export file name"):
        run.record_collected("a", {"../x": "ok"})
    with pytest.raises(LabError, match="side must be"):
        run.side_dir("c")


def test_restore_refused_before_run_a_is_collected(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    with pytest.raises(LabError, match="restore would erase it"):
        run.require_collected("a")
    run.record_collected("a", {"sacct.txt": "JobName|User\nj0001|lab-u001\n"})
    run.require_collected("a")
    (run.side_dir("a") / "sacct.txt").unlink()
    with pytest.raises(LabError, match="restore would erase it"):
        run.require_collected("a")


# ---------------------------------------------------------------- pairing

def test_pair_by_job_key_not_job_id():
    a = lab_run.parse_rows("JobID|JobName|State\n5|j0001|COMPLETED\n6|j0002|FAILED\n7|j0003|COMPLETED\n")
    b = lab_run.parse_rows("JobID|JobName|State\n5|j0002|COMPLETED\n6|j0001|COMPLETED\n8|j0004|COMPLETED\n9||X\n")
    p = lab_run.pair(a, b)
    assert [k for k, _, _ in p["pairs"]] == ["j0001", "j0002"]
    assert p["pairs"][0][1]["JobID"] == "5" and p["pairs"][0][2]["JobID"] == "6"
    assert p["only_a"] == ["j0003"] and p["only_b"] == ["j0004"]
    assert lab_run.parse_rows("") == []


# ---------------------------------------------------------------- drive

class Steps:
    def __init__(self, fail_at=None, abort_at=None, replay_says=None, collect_fails=False, restore_fails=False):
        self.calls, self.fail_at, self.abort_at, self.replay_says = [], fail_at, abort_at, replay_says
        self.collect_fails, self.restore_fails, self.run = collect_fails, restore_fails, None

    def abort_reason(self):
        return "aborted under memory pressure" if self.run and self.run.state == self.abort_at else None

    def _maybe(self, what):
        self.calls.append(what)
        if what == self.fail_at:
            raise RuntimeError(f"{what} broke")

    def snapshot(self):
        self._maybe("snapshot")
        return {"spool": "h1"}

    def apply(self, side):
        self._maybe(f"apply {side}")

    def replay(self, side, run):
        self.run = run
        self._maybe(f"replay {side}")
        run.jobs()  # both sides read the frozen file
        run.report_progress(len(run.jobs()["jobs"]), 0, 0, run.r["length_s"])
        return self.replay_says

    def collect(self, side, run):
        self._maybe(f"collect {side}")
        if self.collect_fails:
            raise RuntimeError("sacct failed")
        return {"sacct.txt": f"JobName|User\nj0001|lab-u001 side {side}\n"}

    def restore(self):
        self._maybe("restore")
        if self.restore_fails:
            raise RuntimeError("restore broke")
        return {"spool_match": True, "db_match": True}

    def compare(self, run):
        self._maybe("compare")

    def report(self, run):
        self._maybe("report")


def test_drive_ab_order_and_collect_before_restore(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    s = Steps()
    lab_run.drive(run, s)
    assert s.calls == ["snapshot", "apply baseline", "replay a", "collect a", "restore", "apply candidate", "replay b", "collect b",
                       "compare", "report"]
    assert run.state == "done" and run.percent() == 100
    assert (run.side_dir("a") / "sacct.txt").is_file() and (run.side_dir("b") / "sacct.txt").is_file()
    assert run.r["snapshot"] == {"spool": "h1"} and run.r["restore"]["spool_match"]
    assert [h["state"] for h in run.r["history"]] == list(lab_run.AB_PATH)


def test_drive_candidate_only(root):
    run = lab_run.Run.create("prod-a", SIM, root, ab=False, clock=Clock())
    s = Steps()
    lab_run.drive(run, s)
    assert s.calls == ["apply candidate", "replay b", "collect b", "report"] and run.state == "done"


def test_drive_abort_during_replay_collects_then_restores(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    s = Steps(replay_says="aborted under memory pressure")
    lab_run.drive(run, s)
    assert run.state == "aborted" and run.r["reason"] == "aborted under memory pressure"
    assert s.calls == ["snapshot", "apply baseline", "replay a", "collect a", "restore"]
    assert run.r["restored_after_abort"] and (run.side_dir("a") / "sacct.txt").is_file()


def test_drive_abort_flag_before_a_state(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    s = Steps(abort_at="collect_b")
    lab_run.drive(run, s)
    assert run.state == "aborted" and run.last_state() == "collect_b" and s.calls[-1] == "restore"
    assert s.calls.count("restore") == 2  # the A/B restore, then back to the snapshot after the abort


def test_drive_failure_before_snapshot_does_not_restore(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    s = Steps(fail_at="snapshot")
    lab_run.drive(run, s)
    assert run.state == "aborted" and run.r["reason"] == "RuntimeError: snapshot broke" and "restore" not in s.calls


def test_drive_candidate_only_abort_does_not_restore(root):
    run = lab_run.Run.create("prod-a", SIM, root, ab=False, clock=Clock())
    s = Steps(fail_at="replay b")
    lab_run.drive(run, s)
    assert run.state == "aborted" and "restore" not in s.calls


def test_drive_abort_records_failed_collect_and_restore(root):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    s = Steps(replay_says="stopped", collect_fails=True, restore_fails=True)
    lab_run.drive(run, s)
    assert run.state == "aborted" and run.r["collect_error"] == "sacct failed"
    assert run.r["restored_after_abort"] is False and run.r["restore_error"] == "restore broke"
    saved = json.loads((run.folder / "run.json").read_text())
    assert saved["restore_error"] == "restore broke"


def test_drive_restore_refused_when_collect_a_left_nothing(root, monkeypatch):
    run = lab_run.Run.create("prod-a", SIM, root, clock=Clock())
    s = Steps()
    s.collect = lambda side, run: {}
    lab_run.drive(run, s)
    assert run.state == "aborted" and "restore would erase it" in run.r["reason"]
