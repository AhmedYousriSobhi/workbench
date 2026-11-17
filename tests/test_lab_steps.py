"""Config lab run, the host side of each state (issue 554 steps 3 and 4): the driver's container, its progress lines, the lab_run
Prometheus file, the governor's flags, the wait for calm before an abort's restore. Fakes only: no Docker, no governor."""
import json
from pathlib import Path

import pytest
import yaml

from benchctl import lab, lab_run, lab_steps
from benchctl.lab import LabError

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "lab"
    lab.import_lab("prod-a", FIX, root=r)
    return r


@pytest.fixture
def run(root):
    return lab_run.Run.create("prod-a", SIM, root, ab=True)


class Proc:
    def __init__(self, lines, rc=0):
        self.stdout, self.rc = iter(lines), rc

    def wait(self):
        return self.rc


def steps(run, root, tmp_path, lines=(), rc=0, **kw):
    seen = []

    def popen(argv):
        seen.append(argv)
        return Proc([*lines], rc)
    s = lab_steps.RunSteps("prod-a", run.r["run"], root=root, gen=tmp_path / "gen", popen=popen, **kw)
    return s, seen


def test_driver_runs_as_an_unlabelled_memory_limited_one_shot_with_read_only_mounts(run, tmp_path):
    argv = lab_steps.driver_argv(run, "a", tmp_path / "gen")
    assert argv[:3] == ["docker", "run", "--rm"] and "--label" not in argv and not any("workbench.class" in a for a in argv)
    assert argv[argv.index("--memory") + 1] == "250m" and argv[argv.index("--network") + 1] == "workbench_default"
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert mounts and all(m.endswith(":ro") for m in mounts)
    assert any(m.endswith(":/run/workbench-flags:ro") for m in mounts) and any("lab_driver.py:/lab_driver.py:ro" in m for m in mounts)
    for bad in ("--privileged", "--cap-add", "/var/run/docker.sock"):
        assert not any(bad in a for a in argv)
    assert lab_steps.driver_name(run.r["run"], "a") in argv


def test_prom_targets_carry_the_hidden_lab_run_label_for_slurmctlds_paths():
    t = lab_steps.prom_targets("prod-a", "20261117-120000", "b")
    assert [x["labels"]["__metrics_path__"] for x in t] == [f"/metrics/{p}" for p in lab_steps.SLURM_PATHS]
    assert {x["labels"]["__lab_run"] for x in t} == {"prod-a/20261117-120000/b"} and all(x["targets"] == ["slurmctld:6817"] for x in t)


def test_prometheus_job_keeps_only_labelled_targets_and_copies_the_label():
    prom = yaml.safe_load((ROOT / "observability" / "prometheus.yml").read_text())
    job = next(j for j in prom["scrape_configs"] if j["job_name"] == "lab-run")
    assert job["file_sd_configs"][0]["files"] == ["/etc/prometheus/generated/lab-run/*.json"]
    rules = job["relabel_configs"]
    assert {"source_labels": ["__lab_run"], "regex": ".+", "action": "keep"} in rules
    assert {"source_labels": ["__lab_run"], "target_label": "lab_run"} in rules


def test_parse_line():
    assert lab_steps.parse_line('@progress {"submitted": 2}') == ("progress", {"submitted": 2})
    assert lab_steps.parse_line('@done {"reason": null}') == ("done", {"reason": None})
    for junk in ("Unable to find image", "@progress {bad", "@done [1]"):
        assert lab_steps.parse_line(junk) is None


def test_replay_turns_progress_into_the_run_and_writes_then_removes_the_prometheus_file(run, root, tmp_path):
    lines = ['pulling\n', '@progress {"submitted": 1, "running": 1, "pending": 0, "replay_s": 30, "paused": false}\n',
             '@progress {"submitted": 2, "running": 1, "pending": 1, "replay_s": 40, "paused": true}\n',
             '@done {"submitted": 2, "running": 0, "pending": 0, "replay_s": 60, "paused": false, "rejected": 1, "reason": null}\n']
    s, seen = steps(run, root, tmp_path, lines)
    prom = tmp_path / "gen" / lab_steps.PROM_DIR / "slurm.json"
    files = []
    orig = s._prom
    s._prom = lambda r, side: (orig(r, side), files.append(prom.exists()))
    run.move("snapshot")
    run.move("apply_baseline")
    run.move("replay_a")
    assert s.replay("a", run) is None
    assert seen and seen[0][0] == "docker" and files == [True, False]
    p = json.loads((run.folder / "progress.json").read_text())
    assert p["submitted"] == 2 and p["paused"] is False and run.r["rejected"] == {"a": 1}


def test_replay_returns_the_drivers_reason_or_says_it_gave_no_result(run, root, tmp_path):
    for _ in ("snapshot", "apply_baseline", "replay_a"):
        run.move(_)
    s, _ = steps(run, root, tmp_path, ['@done {"reason": "aborted under memory pressure (L3)", "paused": true}\n'])
    assert s.replay("a", run) == "aborted under memory pressure (L3)"
    s, _ = steps(run, root, tmp_path, ["docker: no such image\n"], rc=125)
    assert "without a result (exit 125)" in s.replay("a", run)


def test_progress_outside_a_replay_is_ignored(run):
    lab_steps.RunSteps._progress(run, {"submitted": 5})
    assert run.r["counts"]["submitted"] == 0


def test_abort_reason_reads_the_governors_flag(run, root, tmp_path):
    s, _ = steps(run, root, tmp_path)
    assert s.abort_reason() is None
    flags = tmp_path / "gen" / "flags"
    flags.mkdir(parents=True)
    (flags / lab_steps.ABORT_FLAG).write_text("stopped by the operator\n")
    assert s.abort_reason() == "stopped by the operator"
    (flags / lab_steps.ABORT_FLAG).write_text("")
    assert s.abort_reason() == "aborted"
    (flags / lab_steps.ABORT_FLAG).unlink()
    (flags / lab_steps.ABORT_FLAG).mkdir()
    assert s.abort_reason() == "aborted"


def test_wait_calm_waits_for_l1_and_gives_up_when_the_governor_is_gone(run, root, tmp_path):
    levels = iter([3, 2, 1])
    t = [0.0]
    s, _ = steps(run, root, tmp_path, governor=lambda req: {"level": next(levels)}, sleep=lambda x: t.__setitem__(0, t[0] + x),
                 clock=lambda: t[0])
    s.wait_calm(run)
    assert t[0] == 2 * lab_steps.CALM_POLL_S and run.r["restore_pending"] is False

    def gone(req):
        raise OSError("no socket")
    s, _ = steps(run, root, tmp_path, governor=gone, sleep=lambda x: t.__setitem__(0, t[0] + x), clock=lambda: t[0])
    with pytest.raises(LabError, match="the snapshot is kept"):
        s.wait_calm(run)
    assert run.r["restore_pending"] is True
    steps(run, root, tmp_path)[0].wait_calm(run)  # no governor (tests, a dry run): nothing to wait for


def test_compare_and_report_write_their_files(run, root, tmp_path):
    s, _ = steps(run, root, tmp_path)
    run.side_dir("a").mkdir()
    (run.side_dir("a") / "sacct.txt").write_text("JobID|JobName|User\n7|j0001|lab-u001\n8|j0002|lab-u001\n")
    run.side_dir("b").mkdir()
    (run.side_dir("b") / "sacct.txt").write_text("JobID|JobName|User\n7|j0001|lab-u001\n")
    s.compare(run)
    assert json.loads((run.folder / "compare.json").read_text()) == {"pairs": 1, "only_a": ["j0002"], "only_b": []}
    s.report(run)
    rep = json.loads((run.folder / "report.json").read_text())
    assert rep["verdict"] is None and rep["comparable"] is True and rep["seed"] == lab_run.DEFAULT_SEED


def test_apply_needs_an_environment_and_calls_lab_apply(run, root, tmp_path, monkeypatch):
    s, _ = steps(run, root, tmp_path)
    with pytest.raises(LabError, match="no cluster environment"):
        s.apply("baseline")
    said = []
    monkeypatch.setattr(lab_steps.lab_apply, "apply", lambda name, side, env: {"lines": [f"{name} {side} applied"]})
    s, _ = steps(run, root, tmp_path, env=lab_steps.lab_apply.Env(root=root, say=said.append))
    s.apply("candidate")
    assert s.applied == ["candidate"] and said == ["prod-a candidate applied"]


def test_state_steps_are_delegated(run, root, tmp_path):
    calls = []

    class State:
        def snapshot(self):
            calls.append("snapshot")
            return {}

        def restore(self):
            calls.append("restore")
            return {}

        def collect(self, side, r):
            calls.append(f"collect {side}")
            return {}

        def discard(self):
            calls.append("discard")
    s, _ = steps(run, root, tmp_path, state=State())
    s.snapshot(), s.restore(), s.collect("a", run), s.discard()
    assert calls == ["snapshot", "restore", "collect a", "discard"]
    assert steps(run, root, tmp_path)[0].state.dest.name == run.r["run"]  # the default: the run's own snapshot folder


def test_prepare_freezes_a_run_and_reports_the_lab_nodes(root):
    out = lab_steps.prepare("prod-a", SIM, ab=False, target_hours=None, seed=3, root=root)
    run = lab_run.Run.load("prod-a", out["run"], root)
    assert out["lab_nodes"] == run.r["lab_nodes"] >= 1 and run.state == "idle" and run.r["sides"] == ["b"] and out["seed"] == 3
    with pytest.raises(LabError, match="no lab other"):
        lab_steps.prepare("other", SIM, ab=True, target_hours=None, seed=1, root=root)


def test_execute_walks_an_idle_run_and_refuses_one_that_started(root, monkeypatch):
    out = lab_steps.prepare("prod-a", SIM, ab=True, target_hours=None, seed=1, root=root)
    seen = {}
    monkeypatch.setattr(lab_steps.lab_run, "drive", lambda run, st: seen.setdefault("steps", st) and run)
    lab_steps.execute("prod-a", out["run"], env=None, governor=None, root=root)
    assert isinstance(seen["steps"], lab_steps.RunSteps)
    run = lab_run.Run.load("prod-a", out["run"], root)
    run.abort("x")
    with pytest.raises(LabError, match="not idle"):
        lab_steps.execute("prod-a", out["run"], env=None, governor=None, root=root)


def test_a_failed_restore_after_an_abort_keeps_the_snapshot(run):
    calls = []

    class S:
        def abort_reason(self):
            return None

        def snapshot(self):
            return {"spool_files": 1}

        def apply(self, side):
            raise LabError("apply failed")

        def wait_calm(self, r):
            calls.append("wait")

        def restore(self):
            raise LabError("restore failed")

        def discard(self):
            calls.append("discard")
    lab_run.drive(run, S())
    assert run.state == "aborted" and run.r["restored_after_abort"] is False and calls == ["wait"]


def test_abort_reason_waits_while_paused_and_ends_on_resume_or_abort(run, root, tmp_path):
    flags = tmp_path / "gen" / "flags"
    flags.mkdir(parents=True)
    (flags / lab_steps.PAUSE_FLAG).write_text("L2")
    slept = []

    def resume_after_two(s):
        slept.append(s)
        if len(slept) == 2:
            (flags / lab_steps.PAUSE_FLAG).unlink()
    s, _ = steps(run, root, tmp_path, sleep=resume_after_two)
    assert s.abort_reason() is None and slept == [lab_steps.CALM_POLL_S] * 2  # the next state waits for L0-L1 (GV-8)
    (flags / lab_steps.PAUSE_FLAG).write_text("L3")
    s, _ = steps(run, root, tmp_path, sleep=lambda x: (flags / lab_steps.ABORT_FLAG).write_text("aborted under memory pressure (L3)"))
    assert s.abort_reason() == "aborted under memory pressure (L3)"
