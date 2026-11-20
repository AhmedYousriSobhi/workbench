"""Faults and scenario steps during a lab replay (issue 554, owner decisions round 3, Q3): recorded in interference.jsonl only while
a run replays; the scenario engine calls it after a step it applied."""
import json

from benchctl import lab_interference as li
from benchctl import scenario

RID = "20261117-120000"


def setup_run(root, state="replay_a", side="a", active=None):
    folder = root / "prod-a" / "reports" / RID
    folder.mkdir(parents=True)
    (folder / "progress.json").write_text(json.dumps({"state": state, "side": side}))
    (root / li.ACTIVE).write_text(json.dumps(active or {"name": "prod-a", "run": RID}))
    return folder


def test_records_only_while_a_run_replays(tmp_path):
    assert li.record("x", None, "scenario", tmp_path) is False  # no run
    folder = setup_run(tmp_path, state="collect_a")
    assert li.record("x", None, "scenario", tmp_path) is False  # not replaying
    (folder / "progress.json").write_text(json.dumps({"state": "replay_b", "side": "b"}))
    assert li.record("scenario step down", "cn001", "scenario", tmp_path, now=lambda: 0) is True
    assert json.loads((folder / li.INTERFERENCE).read_text()) == {"at": "1970-01-01T00:00:00Z", "action": "scenario step down",
                                                                  "target": "cn001", "side": "b", "by": "scenario"}


def test_refuses_a_bad_active_record(tmp_path):
    setup_run(tmp_path, active={"name": "../x", "run": RID})
    assert li.record("x", None, "scenario", tmp_path) is False


def test_the_scenario_engine_records_an_applied_step(monkeypatch):
    seen = []
    monkeypatch.setattr(scenario, "_apply", lambda step, *a: scenario.Outcome("down cn001", "ok"))
    monkeypatch.setattr(scenario, "side_effects", lambda step, sim=None: [])
    monkeypatch.setattr(scenario.lab_interference, "record", lambda action, target, by: seen.append((action, target, by)))
    scenario.execute(scenario.Step(at=0, target="cn001", do={"down": True}), host=False)
    assert seen == [("scenario step down", "cn001", "scenario")]
    monkeypatch.setattr(scenario, "_apply", lambda step, *a: scenario.Outcome("down cn001", "failed"))
    scenario.execute(scenario.Step(at=0, target="cn001", do={"down": True}), host=False)
    assert len(seen) == 1  # a step that failed changed nothing
