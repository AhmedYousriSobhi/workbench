"""simctl's commands through Typer's runner: exit codes and what the user is told, with the heavy parts faked."""
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from governor import control
from simctl import checks, cli, stack

runner = CliRunner()


@pytest.fixture(autouse=True)
def in_repo(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)


def invoke(*args):
    return runner.invoke(cli.app, list(args))


def test_doctor_exit_code_follows_failures(monkeypatch):
    ok = checks.Check("a", checks.OK, "fine")
    bad = checks.Check("b", checks.FAIL, "broken", fix="do this")
    monkeypatch.setattr(checks, "run_all", lambda: [ok])
    r = invoke("doctor")
    assert r.exit_code == 0 and "[ok  ] a: fine" in r.output
    monkeypatch.setattr(checks, "run_all", lambda: [ok, bad])
    r = invoke("doctor")
    assert r.exit_code == 1 and "[FAIL] b: broken" in r.output and "fix: do this" in r.output


def test_preflight_ok_and_refused(monkeypatch):
    monkeypatch.setattr(checks, "mem_available", lambda: 12 * 1024**3)
    r = invoke("preflight", "--profile", "min")
    assert r.exit_code == 0 and r.output.startswith("OK:")
    monkeypatch.setattr(checks, "mem_available", lambda: 1 * 1024**3)
    r = invoke("preflight", "--profile", "full")
    assert r.exit_code == 1 and r.output.startswith("REFUSED:")


def test_status_reports_the_governor_or_that_it_is_unreachable(tmp_path):
    missing = invoke("status", "--socket", str(tmp_path / "none.sock"))
    assert missing.exit_code == 1 and "governor not reachable" in missing.output
    status = {"ok": True, "level": 3, "dry_run": True, "mem_available": 2 * 2**30, "psi_some": 7.5, "psi_full": 1.0,
              "groups": ["compute", "core"], "violations": ["bad container"], "audit": ["L3: freeze p4"]}
    sock = str(tmp_path / "gov.sock")
    srv = control.serve(sock, lambda cmd, req: status, lambda: {"compute"})
    try:
        r = invoke("status", "--socket", sock)
    finally:
        srv.shutdown()
    assert r.exit_code == 0
    assert "level L3 (dry-run)" in r.output and "avail 2.00 GB" in r.output
    assert "groups: compute, core" in r.output and "SECURITY: bad container" in r.output and "L3: freeze p4" in r.output


def test_compose_writes_the_generated_file(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli.compose, "write", lambda data, sim, compute_nodes: seen.update(n=compute_nodes) or tmp_path / "c.yml")
    r = invoke("compose", "--profile", "min")
    assert r.exit_code == 0 and "services" in r.output and seen["n"] == 4


@pytest.mark.parametrize("cmd,fn,args", [("up", "up", ["--profile", "min"]), ("down", "down", []), ("scale", "scale", ["2"])])
def test_stack_commands_map_the_result_to_the_exit_code(monkeypatch, cmd, fn, args):
    monkeypatch.setattr(stack, "current_profile", lambda: "min")
    for result, code in ((True, 0), (False, 1)):
        monkeypatch.setattr(stack, fn, lambda *a, _r=result, **k: _r)
        assert invoke(cmd, *args).exit_code == code


def test_scale_defaults_to_the_profile_up_last_started(monkeypatch):
    got = {}
    monkeypatch.setattr(stack, "current_profile", lambda: "std")
    monkeypatch.setattr(stack, "scale", lambda sim, profile, n, say=print: got.update(profile=profile, n=n) or True)
    assert invoke("scale", "3").exit_code == 0 and got == {"profile": "std", "n": 3}
    assert invoke("scale", "3", "--profile", "min").exit_code == 0 and got["profile"] == "min"
    assert invoke("scale", "-1").exit_code != 0  # min=0 enforced by Typer


def test_diag_prints_the_bundle_path(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.diag, "write_bundle", lambda out: tmp_path / "bundle.tar.gz")
    r = invoke("diag")
    assert r.exit_code == 0 and r.output.strip().endswith("bundle.tar.gz")


def test_scenario_list_and_unknown_and_dry_run():
    listing = invoke("scenario", "list")
    assert listing.exit_code == 0 and "node-down" in listing.output and "steps" in listing.output
    unknown = invoke("scenario", "run", "no-such")
    assert unknown.exit_code == 1 and "no scenario" in unknown.output
    dry = invoke("scenario", "run", "node-down", "--dry-run")
    assert dry.exit_code == 0 and "bmc-farm" in dry.output and "drain" in dry.output


def test_scenario_run_rejects_an_invalid_scenario(monkeypatch):
    monkeypatch.setattr(cli.scenario, "load", lambda name: {"name": "x", "steps": [{"at": "1s", "target": "nope", "do": {}}]})
    r = invoke("scenario", "run", "x")
    assert r.exit_code == 1 and "invalid: step 1: unknown target 'nope'" in r.output


def test_scenario_run_reports_pass_and_fail(monkeypatch):
    from simctl import scenario

    sc = {"name": "x", "steps": []}
    monkeypatch.setattr(cli.scenario, "load", lambda name: sc)
    for ok, word, code in ((True, "passed", 0), (False, "FAILED", 1)):
        rep = scenario.Report("x", steps=[scenario.Outcome("s", "ok" if ok else "failed")])
        monkeypatch.setattr(cli.scenario, "run", lambda *a, _r=rep, **k: _r)
        r = invoke("scenario", "run", "x")
        assert r.exit_code == code and f"scenario {word}" in r.output


def test_measure_run_saves_and_report_reads_it_back(monkeypatch, tmp_path):
    snap = {"docker:a": 10 << 20, "host:used": 1 << 30}
    monkeypatch.setattr(cli.measure, "sample", lambda duration, interval: [snap, snap])
    monkeypatch.chdir(tmp_path)  # measure.save writes docs/measurements/<label>.json under the cwd
    r = invoke("measure", "run", "idle", "--duration", "1")
    assert r.exit_code == 0 and "2 samples" in r.output
    assert (tmp_path / "docs" / "measurements" / "idle.json").is_file()
    r = invoke("measure", "report", "idle")
    assert r.exit_code == 0 and "docker:a" in r.output and "| Component |" in r.output
    invoke("measure", "run", "loaded")
    both = invoke("measure", "report", "idle", "loaded")
    assert both.exit_code == 0 and "Loaded peak MB" in both.output


def test_ssh_key_commands(monkeypatch, tmp_path):
    dest = tmp_path / "opti-sim_ed25519"
    src = tmp_path / "ssh"
    src.mkdir()
    (src / "id_ed25519").write_text("KEY")
    real_install, real_remove = stack.install_client_key, stack.remove_client_key
    monkeypatch.setattr(stack, "install_client_key", lambda: real_install(dest, src))
    monkeypatch.setattr(stack, "remove_client_key", lambda: real_remove(dest, src))
    r = invoke("ssh-key", "install")
    assert r.exit_code == 0 and "~/.ssh/opti-sim_ed25519" in r.output and dest.exists()
    assert "removed" in invoke("ssh-key", "remove").output
    assert "nothing to remove" in invoke("ssh-key", "remove").output
    (src / "id_ed25519").unlink()
    r = invoke("ssh-key", "install")
    assert r.exit_code == 1 and "benchctl up" in r.output


def test_get_resources_and_json(monkeypatch):
    """`benchctl get nodes|profiles|scenarios` print a table, or JSON with -o json."""
    import json

    from simctl import inventory

    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"cn001": "running", "gpu001": "paused"})
    r = runner.invoke(cli.app, ["get", "nodes"])
    assert r.exit_code == 0 and "cn001" in r.output and "running" in r.output and "absent" in r.output
    rows = json.loads(runner.invoke(cli.app, ["get", "nodes", "-o", "json"]).output)
    assert {"name": "gpu001", "kind": "gpu", "state": "paused"} in rows
    assert "min" in runner.invoke(cli.app, ["get", "profiles"]).output
    assert "node-down" in runner.invoke(cli.app, ["get", "scenarios"]).output


def test_get_services_lists_the_stack(monkeypatch):
    from simctl import inventory

    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"slurmctld": "running"})
    assert "slurmctld" in runner.invoke(cli.app, ["get", "services"]).output
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {})
    assert runner.invoke(cli.app, ["get", "services"]).output.strip() == "none"


def test_node_completion_offers_sim_yaml_nodes():
    from simctl.cli import _node_names

    assert _node_names("gpu") == ["gpu001", "gpu002"]
    assert "cn001" in _node_names("")


def test_exec_and_logs_run_docker_in_the_project_container(monkeypatch):
    import os

    calls = []
    monkeypatch.setattr(os, "execvp", lambda f, a: calls.append(a))
    runner.invoke(cli.app, ["exec", "gpu001", "-u", "alice", "--", "hostname"])
    assert calls[-1] == ["docker", "exec", "-u", "alice", "hpcsim-gpu001-1", "hostname"]
    runner.invoke(cli.app, ["logs", "cn001", "--tail", "5"])
    assert calls[-1] == ["docker", "logs", "--tail", "5", "hpcsim-cn001-1"]


def test_start_stop_open_delegate_to_the_launcher(monkeypatch):
    import subprocess

    seen = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, check=False: seen.append(cmd[1:]) or subprocess.CompletedProcess(cmd, 0))
    for args in (["start", "--profile", "min"], ["stop"], ["open"]):
        assert runner.invoke(cli.app, args).exit_code == 0
    assert seen == [["start", "--profile", "min"], ["stop"], ["open"]]


def test_status_also_says_where_the_cluster_and_web_page_are(monkeypatch, tmp_path):
    from simctl import inventory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(inventory, "service_rows", lambda runner=None: [{"name": "login", "state": "running"}])
    r = runner.invoke(cli.app, ["status", "--socket", str(tmp_path / "none.sock")])
    assert r.exit_code == 1 and "cluster: 1 containers running" in r.output and "web page: stopped" in r.output
    (tmp_path / "compose/generated").mkdir(parents=True)
    (tmp_path / "compose/generated/ui.pid").write_text("1")
    (tmp_path / "compose/generated/ui.log").write_text("opti-sim UI: http://127.0.0.1:8088/?t=x\n")
    assert "web page: http://127.0.0.1:8088/?t=x" in runner.invoke(cli.app, ["status", "--socket", "/nope"]).output
    monkeypatch.setattr(inventory, "service_rows", lambda runner=None: [])
    assert "cluster: stopped" in runner.invoke(cli.app, ["status", "--socket", "/nope"]).output


def test_a_mistyped_verb_gets_a_hint_and_is_never_run(monkeypatch):
    ran = []
    monkeypatch.setattr(stack, "down", lambda *a, **k: ran.append("down") or True)
    r = runner.invoke(cli.app, ["dwon"])
    assert r.exit_code == 2 and "Did you mean 'down'?" in r.output and ran == []
    assert "Did you mean 'nodes'?" in runner.invoke(cli.app, ["get", "nodez"]).output
    nonsense = runner.invoke(cli.app, ["xyzzy"])
    assert nonsense.exit_code == 2 and "Did you mean" not in nonsense.output


def test_refusals_and_errors_go_to_stderr_and_data_to_stdout(monkeypatch):
    monkeypatch.setattr(checks, "mem_available", lambda *a: 0)
    r = runner.invoke(cli.app, ["preflight", "--profile", "min"])
    assert r.exit_code == 1 and "REFUSED" in r.stderr and r.stdout == ""
    ok = runner.invoke(cli.app, ["get", "profiles", "-o", "json"])
    assert "min" in ok.stdout and ok.stderr == ""
    gone = runner.invoke(cli.app, ["status", "--socket", "/nonexistent.sock"])
    assert "governor not reachable" in gone.stderr and "governor not reachable" not in gone.stdout


def test_help_tells_scripts_where_to_look():
    out = runner.invoke(cli.app, ["--help"]).output
    assert "-o json" in out and "stderr" in out and "Exit codes" in out


def test_old_name_prints_one_notice_only_in_a_terminal(monkeypatch):
    import io

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.argv", ["/x/simctl", "status"])
    err = Tty()
    monkeypatch.setattr("sys.stderr", err)
    cli._old_name_notice()
    assert err.getvalue().count("`simctl` is now `benchctl`") == 1
    quiet = io.StringIO()  # not a terminal: scripts see nothing new
    monkeypatch.setattr("sys.stderr", quiet)
    cli._old_name_notice()
    monkeypatch.setattr("sys.argv", ["/x/benchctl", "status"])
    monkeypatch.setattr("sys.stderr", Tty())
    cli._old_name_notice()
    assert quiet.getvalue() == "" and sys.stderr.getvalue() == ""
