"""Per-module timing of a live run (issue 576, V-7): tests/real/verify_times.py."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("verify_times", Path(__file__).resolve().parent / "real" / "verify_times.py")
vt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vt)


def clock(*ticks):
    it = iter(ticks)
    return lambda: next(it)


def test_a_module_costs_from_its_first_setup_to_its_last_teardown():
    r = vt.Recorder({"test_k8s_real"}, clock(0, 10, 100, 100, 250))
    r.start("test_a")          # t=0
    r.finish("test_a")         # t=10
    r.start("test_k8s_real")   # t=100
    r.start("test_k8s_real")   # (second test of the module: start is idempotent)  t=100
    r.finish("test_k8s_real")  # t=250
    rows = {x["module"]: x for x in r.rows()}
    assert rows["test_a"]["seconds"] == 10 and rows["test_a"]["cold_start"] is False
    assert rows["test_k8s_real"]["seconds"] == 150 and rows["test_k8s_real"]["cold_start"] is True
    assert r.rows()[0]["module"] == "test_k8s_real"  # slowest first


def test_failures_and_the_slow_line():
    r = vt.Recorder(set(), clock(0, 5))
    r.start("test_a")
    r.outcome("test_a", True)
    r.finish("test_a")
    assert r.rows()[0]["failed"] == 1
    assert r.slow_line() == "SLOW  test_a 5s (shared)"
    assert vt.Recorder(set()).slow_line() == ""


def test_the_file_keeps_the_last_runs_and_survives_garbage(tmp_path):
    f = tmp_path / "sub" / "t.json"
    for i in range(vt.KEEP_RUNS + 3):
        r = vt.Recorder(set(), clock(0, 1))
        r.start("m")
        r.finish("m")
        r.write(f, f"run{i}")
    runs = json.loads(f.read_text())
    assert len(runs) == vt.KEEP_RUNS and runs[-1]["at"] == f"run{vt.KEEP_RUNS + 2}"
    f.write_text("not json")
    r = vt.Recorder(set(), clock(0, 1))
    r.start("m")
    r.finish("m")
    r.write(f, "x")
    assert len(json.loads(f.read_text())) == 1
