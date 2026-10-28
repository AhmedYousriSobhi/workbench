from governor.ladder import Ladder
from governor.planner import Group, plan, reversible_first
from governor.signals import GB, Signals, parse_meminfo, parse_psi

CALM = Signals(4 * GB, 0.0, 0.0)


def sig(some=0.0, full=0.0, avail=4 * GB):
    return Signals(int(avail), some, full)


def test_parse_psi_and_meminfo():
    p = parse_psi("some avg10=1.50 avg60=0 avg300=0 total=1\nfull avg10=0.25 avg60=0 avg300=0 total=1\n")
    assert p == {"some": 1.5, "full": 0.25}
    assert parse_meminfo("MemAvailable:    2048 kB\n")["MemAvailable"] == 2048 * 1024


def test_l1_immediate():
    assert Ladder().update(sig(some=12), 0) == 1
    assert Ladder().update(sig(avail=1.5 * GB), 0) == 1


def test_l2_requires_sustain():
    lad = Ladder()
    assert lad.update(sig(some=25), 0) == 1
    assert lad.update(sig(some=25), 9) == 1
    assert lad.update(sig(some=25), 10) == 2


def test_l3_and_l4():
    lad = Ladder()
    lad.update(sig(some=35), 0)
    assert lad.update(sig(some=35), 10) == 3
    assert lad.update(sig(full=6), 11) == 4
    assert Ladder().update(sig(avail=0.5 * GB), 0) == 4


def test_l5_when_l4_persists():
    lad = Ladder()
    assert lad.update(sig(full=6), 0) == 4
    assert lad.update(sig(full=6), 30) == 5


def test_deescalation_needs_60s_calm_one_step_at_a_time():
    lad = Ladder()
    lad.update(sig(full=6), 0)  # L4
    assert lad.update(CALM, 20) == 4
    assert lad.update(CALM, 79) == 4
    assert lad.update(CALM, 80) == 3
    assert lad.update(CALM, 139) == 3
    assert lad.update(CALM, 140) == 2


def test_no_flapping_on_brief_calm():
    lad = Ladder()
    lad.update(sig(full=6), 0)
    lad.update(CALM, 30)
    lad.update(sig(full=6), 50)  # pressure returns, calm timer resets
    assert lad.update(CALM, 100) == 4


GROUPS = [Group("app", 1), Group("cn1", 4), Group("load", 5), Group("vm", 6, "vm"), Group("k3d", 3)]


def test_plan_lowest_priority_first():
    acts = plan(4, GROUPS)
    stops = [a.target for a in acts if a.verb in ("stop", "save")]
    assert stops == ["vm", "load", "cn1", "k3d"]
    assert [a.verb for a in acts if a.target == "vm"][-1] == "save"
    assert "app" not in {a.target for a in acts}


def test_plan_l5_spares_p0_p1():
    mocks = GROUPS + [Group("mock", 2), Group("gov", 0)]
    targets = {a.target for a in plan(5, mocks) if a.verb == "stop"}
    assert "mock" in targets and "app" not in targets and "gov" not in targets


def test_reversible_before_destructive():
    verbs = [a.verb for a in reversible_first(plan(4, GROUPS))]
    assert verbs.index("stop") > max(i for i, v in enumerate(verbs) if v in ("freeze", "reclaim"))


# --- security watchdog ---------------------------------------------------------------------

class FakeWatchdog:
    def __init__(self, *scans):
        self.scans, self.calls = list(scans), 0

    def scan(self):
        self.calls += 1
        r = self.scans.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _gov_with(wd, interval=15.0):
    import json
    import pathlib
    import tempfile

    from governor.core import Governor
    from governor.ladder import Ladder
    from governor.signals import FileSampler

    f = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    f.write_text(json.dumps({"mem_available": 8 * 1024**3, "psi_some": 0, "psi_full": 0}))
    return Governor(FileSampler(f), Ladder(), [], lambda a: None, True, wd, interval)


def test_watchdog_reports_new_then_resolved_once():
    wd = FakeWatchdog(["a: privileged"], ["a: privileged"], [])
    g = _gov_with(wd)
    g.step(0)
    g.step(16)  # same finding: not repeated
    g.step(32)
    assert [x for x in g.audit if "privileged" in x] == ["SECURITY: a: privileged", "RESOLVED: a: privileged"]
    assert g.violations == set()


def test_watchdog_is_rate_limited_and_status_lists_violations():
    wd = FakeWatchdog(["b: no memory limit"], [])
    g = _gov_with(wd, 15)
    g.step(0)
    g.step(5)  # not due yet
    assert wd.calls == 1
    assert g.handle("status", {})["violations"] == ["b: no memory limit"]


def test_watchdog_failure_is_logged_once_and_not_fatal():
    wd = FakeWatchdog(RuntimeError("docker down"), RuntimeError("docker down"))
    g = _gov_with(wd)
    g.step(0)
    g.step(16)
    assert sum("watchdog unavailable" in x for x in g.audit) == 1


def test_watchdog_skipped_under_pressure():
    from governor.core import Governor

    wd = FakeWatchdog(["x: privileged"])
    g = _gov_with(wd)
    g._watch(0, 3)
    assert wd.calls == 0 and isinstance(g, Governor)
    g._watch(0, 2)
    assert wd.calls == 1


def test_limit_drift_and_scan(tmp_path):
    import json
    import subprocess

    from governor.watchdog import Watchdog, limit_drift

    info = [{"Name": "/c1", "Id": "x", "HostConfig": {"Memory": 100}, "Mounts": []}]
    (tmp_path / "memory.max").write_text("max\n")
    assert "differs" in limit_drift(info, lambda c: tmp_path)[0]
    (tmp_path / "memory.max").write_text("100\n")
    assert limit_drift(info, lambda c: tmp_path) == []
    assert limit_drift(info, lambda c: tmp_path / "gone") == []  # invisible cgroup: skipped

    def fake(cmd, timeout=15):
        out = "id1\n" if cmd[1] == "ps" else json.dumps(info)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    assert Watchdog(fake, lambda c: tmp_path).scan() == []
