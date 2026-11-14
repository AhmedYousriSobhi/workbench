import json
import random
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, stack, workload
from governor import control

SIM = yaml.safe_load((Path(__file__).resolve().parent.parent / "sim.yaml").read_text())
runner = CliRunner()


def test_a_cpu_job_is_pinned_to_the_node_and_a_gpu_job_holds_its_gpus():
    cmd = workload.command(SIM, "cn002", duration=120)
    assert cmd[:5] == ["docker", "exec", "-u", "alice", "workbench-login-1"]
    line = cmd[-1]
    assert "--nodelist=cn002" in line and "--partition=cpu" in line and "'--wrap=sleep 120'" in line and "--gres" not in line
    assert "--output=/dev/null" in line  # no writable working directory: a default slurm-N.out would fail the job at launch
    gpu = workload.command(SIM, "gpu001", gpus=4, duration=600, user="bob")[-1]
    assert "--partition=gpu" in gpu and "--gres=gpu:4" in gpu and "-u" in workload.command(SIM, "gpu001", user="bob")


@pytest.mark.parametrize("kw,msg", [({"node": "nope"}, "unknown node"), ({"node": "cn001", "gpus": 1}, "has 0 GPU"),
                                    ({"node": "gpu001", "gpus": 99}, "has 8 GPU"), ({"node": "cn001", "duration": 5}, "duration"),
                                    ({"node": "cn001", "user": "root; x"}, "unknown user"), ({"node": "cn001", "cpus": 0}, "cpus")])
def test_what_the_cluster_cannot_honour_is_refused(kw, msg):
    with pytest.raises(workload.WorkloadError, match=msg):
        workload.command(SIM, **kw)


def test_submit_runs_sbatch_on_the_login_node_and_reports_the_job(monkeypatch):
    ran = []
    monkeypatch.setattr(cli.stack, "_run", lambda cmd: ran.append(cmd) or type("R", (), {"returncode": 0, "stdout": "42\n", "stderr": ""})())
    out = runner.invoke(cli.app, ["submit", "cn001", "--duration", "60"])
    assert out.exit_code == 0 and "job 42 submitted" in out.output and "--nodelist=cn001" in ran[-1][-1]
    assert runner.invoke(cli.app, ["submit", "cn001", "--gpus", "1"]).exit_code == 1
    monkeypatch.setattr(cli.stack, "_run", lambda cmd: type("R", (), {"returncode": 1, "stdout": "", "stderr": "invalid node"})())
    assert runner.invoke(cli.app, ["submit", "cn001"]).exit_code == 1
    assert stack  # the seam used above


def test_the_governor_accepts_only_a_bounded_submit_job():
    d = lambda r: control.dispatch(json.dumps(r).encode(), lambda c, q: {"ok": True}, lambda: {"compute"})
    assert d({"cmd": "submit-job", "node": "gpu001", "gpus": 4, "duration": 300}) == {"ok": True}
    assert d({"cmd": "submit-job", "node": "cn001", "gpus": 0, "duration": 300, "user": "alice"}) == {"ok": True}
    for bad in ({"node": "cn001; rm", "gpus": 0, "duration": 60}, {"node": "cn001", "gpus": 9, "duration": 60}, {"node": "cn001", "gpus": 0, "duration": 5},
                {"node": "cn001", "gpus": 0, "duration": 99999}, {"node": "cn001", "gpus": True, "duration": 60}, {"node": "cn001", "gpus": 0},
                {"node": "cn001", "gpus": 0, "duration": 60, "user": "a b"}):
        assert d({"cmd": "submit-job", **bad})["ok"] is False


def test_load_on_copies_the_script_restarts_it_and_load_off_stops_it(monkeypatch):
    cmds = workload.load_on(SIM, rate=12, duration=60, seed=3)
    assert cmds[0][:2] == ["docker", "cp"] and cmds[1] == workload.load_off() and cmds[2][:3] == ["docker", "exec", "-d"]
    assert cmds[2][-6:] == ["--users", "alice,bob", "--gpus", "8", "--seed", "3"]
    for kw in ({"rate": 0}, {"rate": 500}, {"duration": 1}):
        with pytest.raises(workload.WorkloadError):
            workload.load_on(SIM, **kw)
    ran = []
    monkeypatch.setattr(cli.stack, "_run", lambda cmd: ran.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    assert runner.invoke(cli.app, ["load", "on", "--rate", "3"]).exit_code == 0 and len(ran) == 3
    assert runner.invoke(cli.app, ["load", "off"]).exit_code == 0 and ran[-1] == workload.load_off()
    assert runner.invoke(cli.app, ["load", "on", "--rate", "0"]).exit_code == 1


def test_the_user_sim_builds_jobs_for_simulated_users():
    import importlib.util
    import random

    spec = importlib.util.spec_from_file_location("user_sim", Path(__file__).resolve().parent.parent / "images/login/user_sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rng, seen = random.Random(1), set()
    for _ in range(60):
        cmd = mod.pick(rng, ["alice", "bob"], 8, 120)
        assert cmd[:3] == ["runuser", "-u", cmd[2]] and cmd[2] in ("alice", "bob") and "sbatch" in cmd
        seen.add("gpu" if "--partition=gpu" in cmd else "cpu")
        assert "--output=/dev/null" in cmd
        assert ("--gres=gpu" in " ".join(cmd)) == ("--partition=gpu" in cmd)
    assert seen == {"cpu", "gpu"}
    assert all("--partition=cpu" in mod.pick(rng, ["alice"], 0, 60) for _ in range(30))  # no GPU nodes: cpu jobs only


def test_the_user_sim_submits_nothing_while_the_l1_flag_exists(tmp_path, monkeypatch):
    """#476: the governor's pause flag stops new arrivals; they resume when it is removed."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("user_sim", Path(__file__).resolve().parent.parent / "images/login/user_sim.py")
    us = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(us)
    flag = tmp_path / "arrivals.paused"
    monkeypatch.setattr(us, "PAUSED", str(flag))
    monkeypatch.setattr(sys, "argv", ["user-sim", "--seed", "1"])
    submitted = []  # whether the flag existed at each submission

    def fake_run(*cmd):
        if "sbatch" in cmd:
            submitted.append(flag.exists())
        return "0"
    ticks = {"n": 0}

    def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] == 6:
            flag.unlink()  # the governor de-escalated
        if ticks["n"] > 10:
            raise KeyboardInterrupt
    monkeypatch.setattr(us, "run", fake_run)
    monkeypatch.setattr(us.time, "sleep", fake_sleep)
    flag.touch()
    with pytest.raises(KeyboardInterrupt):
        us.main()
    assert len(submitted) == 5 and not any(submitted)  # five arrivals after the flag went; none while it stood


def test_mocks_on_copies_the_demo_client_and_starts_it_with_node_names():
    cmds = workload.mocks_on(SIM, rate=20)
    assert cmds[0][:2] == ["docker", "cp"] and cmds[1] == workload.mocks_off() and cmds[2][:3] == ["docker", "exec", "-d"]
    assert cmds[2][-4:-2] == ["--rate", "20"] and cmds[2][-2] == "--nodes" and cmds[2][-1]
    for rate in (0, 601):
        with pytest.raises(workload.WorkloadError):
            workload.mocks_on(SIM, rate=rate)


def test_load_on_with_mocks_starts_both_and_load_off_stops_both(monkeypatch):
    ran = []
    monkeypatch.setattr(cli.stack, "_run", lambda cmd: ran.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    r = runner.invoke(cli.app, ["load", "on", "--mocks", "--mock-rate", "12"])
    assert r.exit_code == 0 and len(ran) == 6 and "demo client" in r.output
    ran.clear()
    assert runner.invoke(cli.app, ["load", "off"]).exit_code == 0 and ran == [workload.mocks_off(), workload.load_off()]


def _user_sim():
    import importlib.util
    spec = importlib.util.spec_from_file_location("user_sim", Path(__file__).resolve().parent.parent / "images/login/user_sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_rhythm_peaks_at_the_busiest_hour_and_never_stops():  # #680
    us = _user_sim()
    assert us.rhythm(14, 14) == pytest.approx(1.9) and us.rhythm(2, 14) == us.RHYTHM_FLOOR
    assert us.rhythm(22, 22) > us.rhythm(14, 22)  # a persona's own peak
    assert sum(us.rhythm(h / 4, 14) for h in range(96)) / 96 == pytest.approx(1, abs=0.1)  # mean about 1 until the floor bites
    assert us.sim_hour(0, 0) == 8 and us.sim_hour(30, 60, start=0) == 12 and us.sim_hour(60, 60, start=0) == 0  # a day compresses to --day seconds


def test_persona_job_mixes_differ_and_are_repeatable_with_a_seed():
    us = _user_sim()
    users = {"alice": "ml-trainer", "bob": "mpi-researcher"}

    def mix(seed):
        rng, out = random.Random(seed), []
        for _ in range(400):
            cmd, _c = us.plan(rng, users, 8, 120, personas=True)
            out.append((cmd[2], "--partition=gpu" in cmd))
        return out
    assert mix(7) == mix(7)
    runs = mix(7)
    gpu_share = lambda u: sum(g for n, g in runs if n == u) / max(1, sum(1 for n, _ in runs if n == u))
    assert gpu_share("alice") > 0.7 and gpu_share("bob") < 0.2
    assert any("alice-train" in c for c in us.plan(random.Random(1), {"alice": "ml-trainer"}, 8, 120, personas=True)[0])


def test_personas_make_failing_and_cancelled_jobs_and_off_means_none():
    us = _user_sim()
    rng, fails, cancels = random.Random(3), 0, 0
    for _ in range(500):
        cmd, c = us.plan(rng, {"alice": "ml-trainer"}, 8, 120, personas=True)
        fails += "exit 1" in cmd[-1]
        cancels += c is not None
        assert c is None or c >= 5
    assert 20 < fails < 90 and 20 < cancels < 90  # about 10% each
    rng = random.Random(3)
    for _ in range(200):
        cmd, c = us.plan(rng, {"alice": "ml-trainer"}, 8, 120)  # personas off: the old mix, nothing fails or is cancelled
        assert "exit 1" not in cmd[-1] and c is None


def test_the_rhythm_shifts_who_submits_at_a_given_hour():
    us = _user_sim()
    users = {"alice": "ml-trainer", "bob": "mpi-researcher"}

    def alice_share(hour):
        rng = random.Random(5)
        return sum(us.plan(rng, users, 8, 60, hour, True)[0][2] == "alice" for _ in range(600)) / 600
    assert alice_share(22) > 0.6 and alice_share(10) < 0.4  # the trainer works nights, the researcher days


def test_the_user_sim_cancels_a_persona_job_and_keeps_the_cap(monkeypatch):
    us = _user_sim()
    monkeypatch.setattr(us.sys, "argv", ["user-sim", "--seed", "1", "--personas", "--users", "alice:ml-trainer", "--day", "60"])
    monkeypatch.setattr(us, "plan", lambda *a, **k: (["runuser", "-u", "alice", "--", "sbatch"], 0.0))
    ran, n = [], {"i": 0}

    def fake_run(*cmd):
        ran.append(cmd)
        return "41;cluster" if "sbatch" in cmd else "0"
    monkeypatch.setattr(us, "run", fake_run)

    def fake_sleep(_):
        n["i"] += 1
        if n["i"] > 40:
            raise KeyboardInterrupt
    monkeypatch.setattr(us.time, "sleep", fake_sleep)
    clock = iter(x * 0.5 for x in range(10000))
    monkeypatch.setattr(us.time, "monotonic", lambda: next(clock))
    with pytest.raises(KeyboardInterrupt):
        us.main()
    assert ("runuser", "-u", "alice", "--", "scancel", "41") in ran
    assert us.MAX_CANCELS == 2 * us.MAX_PENDING


def test_load_on_passes_personas_and_day_only_when_asked():
    base = workload.load_on(SIM)[2]
    assert "--personas" not in base and "--day" not in base and "alice,bob" in base
    on = workload.load_on(SIM, personas=True, day=600)[2]
    assert "--personas" in on and on[on.index("--day") + 1] == "600" and "alice:ml-trainer,bob:mpi-researcher" in on
    for day in (30, 90000):
        with pytest.raises(workload.WorkloadError, match="day"):
            workload.load_on(SIM, day=day)
