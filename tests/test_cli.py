"""simctl's commands through Typer's runner: exit codes and what the user is told, with the heavy parts faked."""
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
