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
