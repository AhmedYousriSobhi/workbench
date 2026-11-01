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
