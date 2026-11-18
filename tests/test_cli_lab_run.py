"""`benchctl run lab NAME [--ab]`, `benchctl stop lab NAME`, the hidden children the governor runs, and `down`, bare `stop` and
`import lab --replace` while a run is active (issue 554 step 5 and owner decisions round 3). The governor is a fake request
function: no socket, no Docker."""
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, lab, lab_run, lab_steps

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
run = CliRunner().invoke
RUNNING = {"name": "prod-a", "run": "20261117-120000", "state": "replay_a", "active": True, "ab": True, "admit_mb": 302}


@pytest.fixture
def gov(monkeypatch):
    """sent: every request; answers: replies to non-status requests, in order; status: the `lab` block `status` returns."""
    sent, answers, status = [], [], {"lab": None}

    def request(req, path=None, timeout=3):
        sent.append(req)
        if req["cmd"] == "status":
            if isinstance(status["lab"], Exception):
                raise status["lab"]
            return {"ok": True, "lab": status["lab"]}
        a = answers.pop(0) if answers else {"ok": True}
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(cli.checks, "governor_request", request)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return sent, answers, status


def test_run_lab_asks_the_governor_then_watches_status_until_it_started(gov):
    sent, answers, status = gov
    answers.append({"ok": True, "started": True, "state": "preparing"})
    status["lab"] = RUNNING
    r = run(cli.app, ["run", "lab", "prod-a", "--ab", "--target-hours", "1.5", "--seed", "4"])
    assert r.exit_code == 0, r.output
    assert sent[0] == {"cmd": "lab-run", "name": "prod-a", "ab": True, "target_hours": 1.5, "seed": 4}
    assert "preparing the run" in r.output and "run 20261117-120000 of lab prod-a started (A/B, admitted 302 MB)" in r.output
    answers.append({"ok": True, "started": True})
    status["lab"] = {"name": "prod-a", "state": "refused", "reason": "refused: 302 MB would leave 0.8 GB (floor 1.0)"}
    r = run(cli.app, ["run", "lab", "prod-a"])
    assert r.exit_code == 1 and "302 MB would leave" in r.output and sent[-2]["ab"] is False


def test_run_lab_says_when_the_watch_ends_without_an_answer(gov, monkeypatch):
    _sent, _answers, status = gov
    status["lab"] = {"name": "prod-a", "state": "preparing", "active": True}
    assert cli._lab_run_outcome("prod-a", timeout=0.01) == 1
    status["lab"] = OSError("gone")
    assert cli._lab_run_outcome("prod-a", timeout=5) == 1


def test_run_lab_says_one_line_per_refusal(gov):
    sent, answers, _ = gov
    answers.append({"ok": False, "error": "refused at L2: lab runs start only at L0-L1 (GV-8)"})
    r = run(cli.app, ["run", "lab", "prod-a"])
    assert r.exit_code == 1 and "refused at L2" in r.output
    answers.append(OSError("no socket"))
    r = run(cli.app, ["run", "lab", "prod-a"])
    assert r.exit_code == 1 and "governor is not reachable" in r.output and "benchctl start" in r.output
    answers.append({"ok": True, "dry_run": True})
    assert "dry-run mode" in run(cli.app, ["run", "lab", "prod-a"]).output
    n = len(sent)
    for bad in (["run", "lab", "prod-a", "--target-hours", "9"], ["run", "lab", "BAD"]):
        assert run(cli.app, bad).exit_code == 1
    assert len(sent) == n  # refused before the governor is asked


def test_stop_lab_and_plain_stop(gov, monkeypatch):
    sent, answers, _status = gov
    answers.append({"ok": True, "stopping": True, "run": "20261117-120000"})
    r = run(cli.app, ["stop", "lab", "prod-a"])
    assert r.exit_code == 0 and sent == [{"cmd": "lab-stop", "name": "prod-a"}] and "is stopping" in r.output
    answers.append({"ok": False, "error": "no run of lab prod-a is in progress"})
    assert run(cli.app, ["stop", "lab", "prod-a"]).exit_code == 1
    assert run(cli.app, ["stop", "lab"]).exit_code == 2 and run(cli.app, ["stop", "other"]).exit_code == 2
    seen = []
    monkeypatch.setattr(cli, "_launcher", lambda *a: seen.append(a))
    run(cli.app, ["stop"])
    assert seen == [("stop",)]


def test_down_and_stop_refuse_during_a_run_and_name_both_ways_out(gov, monkeypatch):
    sent, _answers, status = gov
    status["lab"] = RUNNING
    seen = []
    monkeypatch.setattr(cli, "_launcher", lambda *a: seen.append(a))
    monkeypatch.setattr(cli.stack, "down", lambda say: seen.append("down") or True)
    monkeypatch.setattr(cli.inventory, "service_rows", list)
    for argv in (["down"], ["stop"], ["down", "--yes"], ["stop", "--yes"]):  # --yes never overrides
        r = run(cli.app, argv)
        assert r.exit_code == cli.RUN_REFUSED, argv
        assert "benchctl stop lab prod-a" in r.output and f"benchctl {argv[0]} --force" in r.output and "remove lab prod-a" in r.output
    assert seen == [] and not [s for s in sent if s["cmd"] == "lab-stop"]


def test_down_force_aborts_without_restore_and_agent_mode_needs_yes(gov, monkeypatch):
    sent, answers, status = gov
    status["lab"] = RUNNING
    seen = []
    monkeypatch.setattr(cli, "_launcher", lambda *a: seen.append(a))
    monkeypatch.setattr(cli.stack, "down", lambda say: seen.append("down") or True)
    monkeypatch.setattr(cli.inventory, "service_rows", list)
    monkeypatch.setattr(cli.teleport, "snapshot", lambda: None)
    monkeypatch.setenv("BENCHCTL_AGENT", "1")
    r = run(cli.app, ["down", "--force"])
    assert r.exit_code == 1 and not [s for s in sent if s["cmd"] == "lab-stop"]  # agent mode: --force needs --yes too
    r = run(cli.app, ["down", "--force", "--yes"])
    assert r.exit_code == 0, r.output
    assert {"cmd": "lab-stop", "name": "prod-a", "force": True} in sent and "snapshot is kept" in r.output and seen == ["down"]
    monkeypatch.delenv("BENCHCTL_AGENT")
    r = run(cli.app, ["stop", "--force"])
    assert seen[-1] == ("stop", "--force")
    answers.append({"ok": False, "error": "no run of lab prod-a is in progress"})
    assert run(cli.app, ["down", "--force"]).exit_code == 1
    answers.append(OSError("gone"))
    assert "stopped answering" in run(cli.app, ["down", "--force"]).output


def test_down_without_a_governor_or_a_run_goes_ahead(gov, monkeypatch):
    _sent, _answers, status = gov
    status["lab"] = OSError("no governor")
    assert cli._active_lab_run() is None
    status["lab"] = {"name": "prod-a", "side": "baseline", "state": "applied"}  # a lab applied, no run
    assert cli._active_lab_run() is None


def test_import_replace_is_refused_during_a_run(gov, tmp_path):
    _, _, status = gov
    status["lab"] = RUNNING
    r = run(cli.app, ["import", "lab", "prod-a", "--from", str(FIX), "--replace"])
    assert r.exit_code == 1 and "waits until it has ended" in r.output


def test_prepare_execute_and_recover_children(tmp_path, monkeypatch, gov):
    root = tmp_path / "lab"
    lab.import_lab("prod-a", FIX, root=root)
    monkeypatch.setattr(lab, "ROOT", root)
    monkeypatch.setattr(cli, "_base_sim", lambda: yaml.safe_load((ROOT / "sim.yaml").read_text()))
    r = run(cli.app, ["run", "lab", "prod-a", "--prepare", "--ab", "-o", "json"])
    assert r.exit_code == 0, r.output
    p = json.loads(r.output)
    assert p["ab"] is True and p["seed"] == lab_run.DEFAULT_SEED and p["lab_nodes"] >= 1
    assert "jobs" in run(cli.app, ["run", "lab", "prod-a", "--prepare"]).output
    assert "--execute needs --run" in run(cli.app, ["run", "lab", "prod-a", "--execute"]).output
    assert "--recover needs --run" in run(cli.app, ["run", "lab", "prod-a", "--recover"]).output
    states = iter(["done", "aborted"])

    class R:
        def __init__(self, restored=None):
            self.state, self.r = next(states, "aborted"), {"restored_after_abort": restored}
    seen = []
    monkeypatch.setattr(cli, "_lab_env", lambda: "env")
    monkeypatch.setattr(lab_steps, "execute", lambda name, rid, env, governor: (seen.append((name, rid, env, governor({"cmd": "status"}))), R())[1])
    assert run(cli.app, ["run", "lab", "prod-a", "--execute", "--run", p["run"]]).exit_code == 0
    assert run(cli.app, ["run", "lab", "prod-a", "--execute", "--run", p["run"]]).exit_code == 1
    assert seen[0][:3] == ("prod-a", p["run"], "env")
    outs = iter([True, False])
    monkeypatch.setattr(lab_steps, "recover", lambda name, rid, governor: (governor({"cmd": "status"}), R(next(outs)))[1])
    assert run(cli.app, ["run", "lab", "prod-a", "--recover", "--run", p["run"]]).exit_code == 0
    assert run(cli.app, ["run", "lab", "prod-a", "--recover", "--run", p["run"]]).exit_code == 1
    assert run(cli.app, ["run", "lab", "nope", "--prepare"]).exit_code == 1


def test_status_line_shows_the_lab_and_its_run():
    assert cli._lab_line({"name": "prod-a", "side": "baseline"}) == "lab prod-a (baseline) applied"
    line = cli._lab_line({"name": "prod-a", "side": "candidate", "run": "20261117-120000", "state": "replay_b", "percent": 63,
                          "replay_side": "b", "submitted": 40, "running": 7, "pending": 3, "paused_s": 12.0, "paused": True,
                          "restore_pending": True, "reason": "aborted under memory pressure (L3)"})
    assert line.startswith("lab prod-a (candidate) applied; run 20261117-120000: replay_b 63% side b, 40 submitted, 7 running, 3 pending")
    assert "paused 12 s (PAUSED)" in line and "restore waits for L0-L1" in line and line.endswith(": aborted under memory pressure (L3)")
    assert "refused: no trace" in cli._lab_line({"name": "prod-a", "state": "refused", "reason": "refused: no trace"})


def test_repeat_starts_the_run_again_only_after_the_last_one_is_done(gov, monkeypatch):
    sent, _answers, status = gov
    status["lab"] = RUNNING
    states = iter(["done", "done"])
    monkeypatch.setattr(cli, "_wait_run_end", lambda name: next(states))
    r = run(cli.app, ["run", "lab", "prod-a", "--ab", "--repeat", "3"])
    assert r.exit_code == 0, r.output
    assert [q["cmd"] for q in sent if q["cmd"] == "lab-run"] == ["lab-run"] * 3
    assert "repeat 1 of 3" in r.output and "repeat 3 of 3" in r.output


def test_repeat_stops_when_a_run_did_not_finish(gov, monkeypatch):
    sent, _answers, status = gov
    status["lab"] = RUNNING
    monkeypatch.setattr(cli, "_wait_run_end", lambda name: "aborted")
    r = run(cli.app, ["run", "lab", "prod-a", "--repeat", "3"])
    assert r.exit_code == 1 and "repeat 1 of 3 ended aborted: not starting the next one" in r.output
    assert sum(q["cmd"] == "lab-run" for q in sent) == 1


def test_repeat_is_bounded():
    assert run(cli.app, ["run", "lab", "prod-a", "--repeat", "0"]).exit_code != 0
    assert run(cli.app, ["run", "lab", "prod-a", "--repeat", "11"]).exit_code != 0


def test_wait_run_end_polls_the_newest_runs_record_until_it_is_done(tmp_path, monkeypatch):
    folder = tmp_path / "reports"
    old, new = folder / "20261117-100000", folder / "20261117-120000"
    for d, st in ((old, "done"), (new, "replay_a")):
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"state": st}))
    monkeypatch.setattr(lab_run, "reports_dir", lambda name, root=None: folder)
    waits = []

    def sleep(s):
        waits.append(s)
        (new / "run.json").write_text(json.dumps({"state": "done"}))
    assert cli._wait_run_end("prod-a", poll=5, sleep=sleep) == "done" and waits == [5]
