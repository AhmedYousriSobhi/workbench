from benchctl import slurmtrace
from benchctl.scenario import Outcome, Step


def fake(table):
    def runner(cmd):
        for key, (rc, out) in table.items():
            if key in " ".join(cmd):
                return type("R", (), {"returncode": rc, "stdout": out, "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    return runner


def test_nodes_of_lists_every_touched_node_once():
    sc = {"steps": [{"do": {"submit": {"node": "cn001"}}}, {"do": {"slurmd_restart": ["cn001", "cn002"]}}, {"do": {"cancel": True}}]}
    assert slurmtrace.nodes_of(sc) == ["cn001", "cn002"]


def test_sources_and_state_text():
    assert slurmtrace.source("slurmstepd: error x") == "slurmstepd" and slurmtrace.source("slurmctld: x") == "slurmctld"
    run = fake({"squeue": (0, "5 RUNNING cn001\n"), "pgrep -x slurmstepd": (0, "88\n"), "pgrep -x slurmd": (1, "")})
    assert slurmtrace.jobs(run) == ["5 RUNNING cn001"]
    assert slurmtrace.stepd_pids("cn001", run) == "88" and slurmtrace.slurmd_pids("cn001", run) == "none"
    assert slurmtrace.jobs(fake({"squeue": (1, "")})) == ["squeue: no answer from slurmctld"]


def test_tracer_prints_before_log_lines_and_after():
    said = []
    run = fake({"squeue": (0, "5 RUNNING cn001\n"), "pgrep": (0, "7\n"),
                "docker logs --since": (0, "slurmd: Slurmd shutdown completing\nmunge noise\n")})
    t = slurmtrace.Tracer(["cn001"], say=said.append, runner=run, clock=lambda: 100.0)
    t.step(Step(0, "slurm", {}), Outcome("x", "ok"))
    text = "\n".join(said)
    assert "before: jobs: 5 RUNNING cn001" in text and "after:" in text and "Slurmd shutdown completing" in text
    assert "munge" not in text


def test_tracer_caps_long_logs():
    said = []
    lines = "\n".join(f"slurmd: line {i}" for i in range(30))
    t = slurmtrace.Tracer([], say=said.append, runner=fake({"docker logs": (0, lines)}), clock=lambda: 1.0)
    t.step(Step(0, "slurm", {}), Outcome("x", "ok"))
    assert any("more lines" in s for s in said)


def test_deferred_trace_covers_the_time_between_two_steps():
    entries, calls, t = [], [], [100.0]

    def collector(nodes, since):
        calls.append(since)
        return {"jobs": [f"read {len(calls)}"], "nodes": {}, "logs": {"workbench-slurmctld-1": [f"slurmctld: line {len(calls)}"]}}

    d = slurmtrace.Deferred(["cn001"], collector, entries.append, clock=lambda: t[0])
    d.before_step(Step(0, "slurm", {"a": 1}))  # nothing ran yet: no entry
    assert entries == []
    t[0] = 150.0
    d.before_step(Step(1, "slurm", {"b": 2}))
    assert len(entries) == 1 and "'a': 1" in entries[0]["label"] and "read 1" in entries[0]["before"] and "read 2" in entries[0]["after"]
    assert entries[0]["logs"][0]["line"] == "slurmctld: line 2" and calls[1] == 100.0  # since the previous step
    d.finish()
    assert len(entries) == 2 and "'b': 2" in entries[1]["label"] and calls[2] == 150.0


def test_deferred_trace_skips_when_nothing_can_be_read():
    entries = []
    d = slurmtrace.Deferred([], lambda n, s: None, entries.append)
    d.before_step(Step(0, "slurm", {}))
    d.finish()
    assert entries == []
