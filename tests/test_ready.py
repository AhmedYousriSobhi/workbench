"""Readiness gates (issue 449): `running` is not `ready`. Fake runner and fake HTTP, nothing live."""
import subprocess

from benchctl import ready


def cp(code=0, out=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr="")


def dc(*a):
    return ["dc", *a]


def test_probe_waits_for_scontrol_ping_and_for_each_mock_to_answer():
    seen = {"n": 0}

    def runner(cmd):
        seen["n"] += 1
        return cp(0, "Slurmctld(primary) at slurmctld is UP") if seen["n"] >= 3 else cp(1, "")  # the controller answers on the third try

    sleeps = []
    late = ready.wait_ready(["slurmctld", "bcm-mock", "prometheus"], runner, dc, 60, sleeps.append, http=lambda port: port == 8081)
    assert late == [] and len(sleeps) == 2  # prometheus has no check: never waited on


def test_probe_reports_what_never_answered():
    t = iter(range(0, 1000, 30))
    import time as _t
    real = _t.monotonic
    _t.monotonic = lambda: next(t)
    try:
        late = ready.wait_ready(["bmc-farm", "jira-mock"], lambda c: cp(), dc, 50, lambda s: None, http=lambda p: p == 8080)
    finally:
        _t.monotonic = real
    assert late == ["bmc-farm"]
