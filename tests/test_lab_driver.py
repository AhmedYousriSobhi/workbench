"""Config lab replay driver (issue 554 step 3): the sbatch command of each job, the pause and abort flags, progress lines.
The driver runs with a fake runner, clock and flags: no Slurm, no container."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("lab_driver", ROOT / "images" / "slurm-ctl" / "lab_driver.py")
drv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drv)


def job(key="j0001", user="lab-u001", submit_s=0.0, run_s=30, limit_s=120, kind="completed", exit_code=0, **kw):
    return {"key": key, "user": user, "account": "physics", "qos": "normal", "partition": "cpu", "tres": "cpu=4,mem=8G,node=1",
            "nodes": 1, "submit_s": submit_s, "run_s": run_s, "limit_s": limit_s, "kind": kind, "exit_code": exit_code, "signal": 0, **kw}


def test_lab_uid_is_derived_from_the_name_and_refuses_anything_else():
    assert drv.lab_uid("lab-u001") == 30001 and drv.lab_uid("lab-u0042") == 30042
    for bad in ("alice", "lab-u1", "lab-u001;rm", "root"):
        with pytest.raises(ValueError):
            drv.lab_uid(bad)


def test_body_sleeps_and_exits_with_the_outcome():
    assert drv.body(job()) == "sleep 30; exit 0"
    assert drv.body(job(kind="failed", exit_code=3)) == "sleep 30; exit 3"
    assert drv.body(job(kind="oom", exit_code=0)) == "sleep 30; exit 137"
    assert drv.body(job(kind="timeout", limit_s=90)) == f"sleep {2 * 60 + drv.TIMEOUT_GRACE_S}; exit 0"  # past the limit (2 min)
    assert drv.body(job(kind="timeout", limit_s=None)) == "sleep 30; exit 0"
    assert drv.body(job(exit_code=999)) == "sleep 30; exit 255"


def test_sbatch_argv_runs_as_the_lab_user_with_the_key_as_job_name():
    argv = drv.sbatch_argv(job(limit_s=61))
    assert argv[:4] == ["runuser", "-u", "lab-u001", "--"]
    assert "--job-name=j0001" in argv and "--account=physics" in argv and "--qos=normal" in argv and "--partition=cpu" in argv
    assert "--time=2" in argv and "--cpus-per-task=4" in argv and "--mem=8192M" in argv
    assert argv[-2:] == ["--wrap", "sleep 30; exit 0"]
    assert "--output=/dev/null" in argv and "--chdir=/tmp" in argv  # nodes need no home directory for the lab user


def test_sbatch_argv_leaves_out_empty_and_unsafe_values_and_refuses_a_production_user():
    argv = drv.sbatch_argv(job(account="", qos="a b", limit_s=None))
    assert not any(a.startswith(("--account", "--qos", "--time")) for a in argv)
    with pytest.raises(ValueError):
        drv.sbatch_argv(job(user="alice"))


def test_tres_args_split_per_node_and_prefer_typed_gres():
    assert drv.tres_args("cpu=8,mem=1T,node=2,gres/gpu=4,gres/gpu:a100=4", 2) == ["--cpus-per-task=4", "--mem=524288M", "--gres=gpu:a100:2"]
    assert drv.tres_args("cpu=2,gres/gpu=2,gres/mps=100", 1) == ["--cpus-per-task=2", "--gres=gpu:2,mps:100"]
    assert drv.tres_args("billing=3,mem=bad,gres/x;y=1,gres/gpu=0", 1) == []
    assert drv.tres_args("", 0) == []


class Fake:
    """A fake Slurm: sbatch answers with growing ids (or refuses a job's key), squeue lists the jobs not yet ended."""

    def __init__(self, refuse=(), ends_after=2):
        self.calls, self.next, self.refuse, self.ends_after, self.queue, self.polls = [], 100, set(refuse), ends_after, {}, 0

    def __call__(self, argv):
        self.calls.append(argv)
        if "sbatch" in argv:
            key = next(a.split("=", 1)[1] for a in argv if a.startswith("--job-name="))
            if key in self.refuse:
                return 1, "sbatch: error: Invalid partition name specified"
            self.next += 1
            self.queue[key] = "PENDING"
            return 0, f"{self.next}\n"
        if argv[0] == "squeue":
            self.polls += 1
            if self.polls > self.ends_after:
                self.queue.clear()
            return 0, "".join(f"{k} {s}\n" for k, s in self.queue.items()) + "other RUNNING\n"
        return 0, ""


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def replay(jobs, flags=None, fake=None, **kw):
    clock, out = Clock(), []
    flags = flags if flags is not None else {}
    fake = fake or Fake()
    r = drv.Replay({"jobs": jobs, "length_s": 60}, run=fake, clock=clock, sleep=clock.sleep,
                   flag=lambda n: flags(n, clock.t) if callable(flags) else flags.get(n), say=out.append, **kw)
    return r, r(), out, fake, clock


def test_replay_submits_every_job_at_its_time_and_ends_when_the_queue_is_empty():
    r, res, out, fake, _ = replay([job("j0002", submit_s=10), job("j0001", submit_s=0)], fake=Fake(ends_after=4))
    subs = [a for a in fake.calls if "sbatch" in a]
    assert [next(x for x in a if x.startswith("--job-name")) for a in subs] == ["--job-name=j0001", "--job-name=j0002"]
    assert res["reason"] is None and res["submitted"] == 2 and res["rejected"] == 0
    assert out[0].startswith("@progress ") and out[-1].startswith("@done ")
    assert json.loads(out[-1].removeprefix("@done "))["submitted"] == 2
    assert r.counts == {"running": 0, "pending": 0}


def test_replay_counts_rejected_jobs_and_keeps_going():
    _, res, _, _, _ = replay([job("j0001"), job("j0002")], fake=Fake(refuse={"j0001"}))
    assert res["submitted"] == 1 and res["rejected"] == 1 and res["rejected_keys"] == ["j0001"]
    assert "Invalid partition" in res["rejected_example"]


def test_the_pause_flag_stops_the_replay_clock_and_arrivals_paused_is_ignored():
    def flags(name, t):
        if name == "arrivals.paused":
            raise AssertionError("the driver must never read the L1 flag")
        return "" if name == drv.PAUSE_FLAG and 2 <= t < 12 else None
    seen = {}

    class Timed(Fake):
        def __call__(self, argv):
            if "sbatch" in argv:
                seen["at"] = clock.t
            return super().__call__(argv)
    clock = Clock()
    r = drv.Replay({"jobs": [job("j0001", submit_s=5)], "length_s": 60}, run=Timed(ends_after=3), clock=clock, sleep=clock.sleep,
                   flag=lambda n: flags(n, clock.t), say=lambda s: None)
    res = r()
    assert res["paused_s"] == pytest.approx(10, abs=1)
    assert seen["at"] >= 15 and res["submitted"] == 1  # 5 s of lab time plus the 10 s pause


def test_the_abort_flag_ends_the_replay_and_cancels_its_jobs():
    def flags(name, t):
        return "aborted under memory pressure (L3)" if name == drv.ABORT_FLAG and t >= 3 else None
    _, res, out, fake, _ = replay([job("j0001"), job("j0002", submit_s=50)], flags=flags)
    assert res["reason"] == "aborted under memory pressure (L3)"
    assert ["scancel", "101"] in fake.calls and res["submitted"] == 1
    assert json.loads(out[-1].removeprefix("@done "))["reason"].startswith("aborted")


def test_an_empty_abort_flag_still_aborts_and_a_deadline_ends_a_stuck_replay():
    _, res, _, _, _ = replay([job()], flags={drv.ABORT_FLAG: ""})
    assert res["reason"] == "aborted"
    _, res, _, _, clock = replay([job()], fake=Fake(ends_after=10**6), deadline_s=20)
    assert "did not end within 20 s" in res["reason"] and clock.t <= 25


def test_setup_installs_munge_and_creates_the_lab_users_with_derived_uids():
    calls = []

    def run(argv):
        calls.append(argv)
        return (9, "exists") if argv[-1] == "lab-u002" else (0, "")
    assert drv.setup(["lab-u001", "lab-u002"], run) == []
    assert ["useradd", "-M", "-u", "30001", "-s", "/bin/sh", "lab-u001"] in calls
    assert calls[0][0] == "install" and "munged --force" in calls[1][-1]
    assert drv.setup(["lab-u001"], lambda a: (1, "no munge")) and len(drv.setup(["lab-u001"], lambda a: (1, "x"))) == 3


def test_read_flag_reads_the_flags_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(drv, "FLAGS", str(tmp_path))
    assert drv._read_flag(drv.ABORT_FLAG) is None
    (tmp_path / drv.ABORT_FLAG).write_text("stopped by the operator")
    assert drv._read_flag(drv.ABORT_FLAG) == "stopped by the operator"
    (tmp_path / "dir").mkdir()
    assert drv._read_flag("dir") == ""


def test_main_reports_a_setup_failure_and_a_clean_run(tmp_path, monkeypatch, capsys):
    p = tmp_path / "jobs.json"
    p.write_text(json.dumps({"jobs": [], "length_s": 10}))
    monkeypatch.setattr(drv, "run_cmd", lambda argv: (1, "munge missing"))
    assert drv.main(["--jobs", str(p)]) == 1
    assert "could not start" in capsys.readouterr().out
    monkeypatch.setattr(drv, "run_cmd", lambda argv: (0, ""))
    monkeypatch.setattr(drv, "FLAGS", str(tmp_path / "flags"))
    monkeypatch.setattr(drv.time, "sleep", lambda s: None)
    assert drv.main(["--jobs", str(p), "--deadline", "5"]) == 0
    assert '"reason": null' in capsys.readouterr().out


def test_run_cmd_reports_a_missing_program():
    rc, out = drv.run_cmd(["/nonexistent/lab-driver-tool"])
    assert rc == 127 and out
