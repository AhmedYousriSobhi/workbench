import json
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
