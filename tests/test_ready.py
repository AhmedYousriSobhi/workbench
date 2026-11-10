"""Readiness gates (issue 449): `running` is not `ready`. Fake runner and fake HTTP, nothing live."""
import subprocess

from benchctl import compose, ready


def cp(code=0, out=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr="")


def dc(*a):
    return ["dc", *a]


def test_mode_defaults_to_probe_and_ignores_unknown_values(monkeypatch):
    monkeypatch.delenv(ready.MODE_ENV, raising=False)
    assert ready.mode() == "probe"
    monkeypatch.setenv(ready.MODE_ENV, "healthcheck")
    assert ready.mode() == "healthcheck"
    monkeypatch.setenv(ready.MODE_ENV, "nonsense")
    assert ready.mode() == "probe"


def test_probe_waits_for_scontrol_ping_and_for_each_mock_to_answer():
    seen = {"n": 0}

    def runner(cmd):
        seen["n"] += 1
        return cp(0, "Slurmctld(primary) at slurmctld is UP") if seen["n"] >= 3 else cp(1, "")  # the controller answers on the third try

    sleeps = []
    late = ready.wait_ready(["slurmctld", "bcm-mock", "prometheus"], runner, dc, 60, sleeps.append, http=lambda port: port == 8081, how="probe")
    assert late == [] and len(sleeps) == 2  # prometheus has no check: never waited on


def test_probe_reports_what_never_answered():
    t = iter(range(0, 1000, 30))
    import time as _t
    real = _t.monotonic
    _t.monotonic = lambda: next(t)
    try:
        late = ready.wait_ready(["bmc-farm", "jira-mock"], lambda c: cp(), dc, 50, lambda s: None, http=lambda p: p == 8080, how="probe")
    finally:
        _t.monotonic = real
    assert late == ["bmc-farm"]


def test_healthcheck_mode_waits_for_docker_to_say_healthy():
    answers = iter(["slurmctld starting\nbcm-mock healthy", "slurmctld healthy\nbcm-mock healthy"])
    late = ready.wait_ready(["slurmctld", "bcm-mock"], lambda c: cp(0, next(answers)), dc, 60, lambda s: None, how="healthcheck")
    assert late == []


def test_healthchecks_only_in_healthcheck_mode_and_only_on_the_checked_services(monkeypatch):
    from pathlib import Path

    import yaml
    sim = yaml.safe_load(Path("sim.yaml").read_text())
    prof = compose.load_profile("min")
    monkeypatch.delenv(ready.MODE_ENV, raising=False)
    plain = compose.generate(sim, prof)["services"]
    assert not any("healthcheck" in plain[n] for n in ready.CHECKED if n in plain)
    monkeypatch.setenv(ready.MODE_ENV, "healthcheck")
    hc = compose.generate(sim, prof)["services"]
    assert "scontrol ping" in hc["slurmctld"]["healthcheck"]["test"][1]
    assert not any("healthcheck" in v for n, v in hc.items() if n not in ready.CHECKED and n != "mariadb")
