"""`benchctl run lab NAME [--ab]`, `benchctl stop lab NAME` and the hidden children the governor runs (issue 554 step 5).
The governor is a fake request function: no socket, no Docker."""
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, lab, lab_run, lab_steps

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
run = CliRunner().invoke


@pytest.fixture
def gov(monkeypatch):
    sent, answers = [], []

    def request(req, path=None, timeout=3):
        sent.append(req)
        a = answers.pop(0) if answers else {"ok": True}
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(cli.checks, "governor_request", request)
    return sent, answers


def test_run_lab_asks_the_governor_and_returns(gov):
    sent, answers = gov
    answers.append({"ok": True, "started": True, "run": "20261117-120000", "admit_mb": 302, "ab": True})
    r = run(cli.app, ["run", "lab", "prod-a", "--ab", "--target-hours", "1.5", "--seed", "4"])
    assert r.exit_code == 0, r.output
    assert sent == [{"cmd": "lab-run", "name": "prod-a", "ab": True, "target_hours": 1.5, "seed": 4}]
    assert "run 20261117-120000 of lab prod-a started (A/B, admitted 302 MB)" in r.output and "benchctl stop lab prod-a" in r.output
    answers.append({"ok": True, "run": "20261117-120001", "admit_mb": 263, "ab": False})
    r = run(cli.app, ["run", "lab", "prod-a"])
    assert sent[-1] == {"cmd": "lab-run", "name": "prod-a", "ab": False} and "candidate only" in r.output


def test_run_lab_says_one_line_per_refusal(gov):
    sent, answers = gov
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
    sent, answers = gov
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


def test_prepare_and_execute_children(tmp_path, monkeypatch, gov):
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
    states = iter(["done", "aborted"])

    class R:
        def __init__(self):
            self.state = next(states)
    seen = []
    monkeypatch.setattr(cli, "_lab_env", lambda: "env")
    monkeypatch.setattr(lab_steps, "execute", lambda name, rid, env, governor: (seen.append((name, rid, env, governor({"cmd": "status"}))), R())[1])
    assert run(cli.app, ["run", "lab", "prod-a", "--execute", "--run", p["run"]]).exit_code == 0
    assert run(cli.app, ["run", "lab", "prod-a", "--execute", "--run", p["run"]]).exit_code == 1
    assert seen[0][:3] == ("prod-a", p["run"], "env")
    assert run(cli.app, ["run", "lab", "nope", "--prepare"]).exit_code == 1


def test_status_line_shows_the_lab_and_its_run():
    assert cli._lab_line({"name": "prod-a", "side": "baseline"}) == "lab prod-a (baseline) applied"
    line = cli._lab_line({"name": "prod-a", "side": "candidate", "run": "20261117-120000", "state": "replay_b", "percent": 63,
                          "replay_side": "b", "submitted": 40, "running": 7, "pending": 3, "paused_s": 12.0, "paused": True,
                          "restore_pending": True, "reason": "aborted under memory pressure (L3)"})
    assert line.startswith("lab prod-a (candidate) applied; run 20261117-120000: replay_b 63% side b, 40 submitted, 7 running, 3 pending")
    assert "paused 12 s (PAUSED)" in line and "restore waits for L0-L1" in line and line.endswith(": aborted under memory pressure (L3)")
