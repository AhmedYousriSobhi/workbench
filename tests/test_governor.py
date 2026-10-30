from governor.core import Governor
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


# --- inventory refresh and annotations -----------------------------------------------------

def test_refresh_adds_and_drops_groups_and_acts_on_newcomers_at_current_level(tmp_path):
    from pathlib import Path

    from governor.core import Governor
    from governor.ladder import Ladder
    from governor.planner import Group
    from governor.signals import FileSampler

    sig = tmp_path / "s.json"
    sig.write_text('{"mem_available": 8589934592, "psi_some": 25, "psi_full": 0}')  # L2: refresh allowed
    inv = [[Group("old", 4)]]
    acted = []

    class Ex:
        def __call__(self, a):
            acted.append(str(a))

        def refresh(self, groups, cgroups):
            self.seen = [g.name for g in groups]

    ex = Ex()
    groups = [Group("old", 4), Group("vm1", 6, "vm")]
    g = Governor(FileSampler(sig), Ladder(), groups, ex, False, None, 15,
                 inventory=lambda: (inv[0], {"x": Path("/nonexistent")}), refresh_interval=5)
    g.step(0)
    assert g.step(11) == 2  # 25% PSI held for the sustain window: L2 throttles P4/P5
    inv[0] = [Group("old", 4), Group("fresh", 5)]
    acted.clear()
    g.step(17)
    assert {gr.name for gr in groups} == {"old", "fresh", "vm1"}  # vm survives: it is configured, not found
    assert set(ex.seen) == {"old", "fresh", "vm1"}
    assert any("fresh" in a for a in acted) and not any("old" in a for a in acted)
    inv[0] = [Group("fresh", 5)]
    g.step(23)
    assert "old" not in g.group_names()
    assert any(m == "inventory: - old" for m in g.audit)


def test_executor_refresh_opens_new_and_forgets_removed(tmp_path):
    from governor.executors import Executor
    from governor.memlock import MitigationPath
    from governor.planner import Group

    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / "cgroup.freeze").write_text("0")
    path = MitigationPath()
    ex = Executor([Group("a", 4)], {"a": a}, path)
    assert path.has(str(a / "cgroup.freeze"))
    ex.refresh([Group("b", 4)], {"b": b})
    assert path.has(str(b / "cgroup.freeze")) and not path.has(str(a / "cgroup.freeze"))


def test_annotator_posts_in_order_and_survives_failures():
    import time

    from governor.annotate import Annotator

    posted = []

    def post(url, token, body):
        if body["text"] == "boom":
            raise OSError("down")
        posted.append((url, token, body["text"], body["tags"]))

    an = Annotator("http://g", "tok", post)
    for t in ("one", "boom", "two"):
        an.emit(t)
    deadline = time.time() + 5
    while len(posted) < 2 and time.time() < deadline:
        time.sleep(0.05)
    an.close()
    assert [p[2] for p in posted] == ["one", "two"]
    assert posted[0][:2] == ("http://g", "tok") and "workbench" in posted[0][3]
    assert an.failed is False  # recovered after the successful post


def test_annotator_paused_holds_events_until_resumed():
    import time

    from governor.annotate import Annotator

    got = []
    an = Annotator("u", "t", lambda u, t, b: got.append(b["text"]))
    an.paused.set()
    an.emit("held")
    time.sleep(0.8)
    assert got == []
    an.paused.clear()
    deadline = time.time() + 5
    while not got and time.time() < deadline:
        time.sleep(0.05)
    an.close()
    assert got == ["held"]


def test_governor_forwards_audit_to_annotator_and_pauses_at_l3():
    from governor.core import Governor
    from governor.ladder import Ladder
    from governor.planner import Group

    class Rec:
        def __init__(self):
            self.texts = []
            self.paused = threading_event()

        def emit(self, t):
            self.texts.append(t)

    def threading_event():
        import threading
        return threading.Event()

    import json
    import pathlib
    import tempfile

    from governor.signals import FileSampler

    f = pathlib.Path(tempfile.mkdtemp()) / "s.json"
    f.write_text(json.dumps({"mem_available": 1, "psi_some": 0, "psi_full": 0}))
    rec = Rec()
    g = Governor(FileSampler(f), Ladder(), [Group("c", 4)], lambda a: None, True, annotator=rec)
    g.step(0)
    assert any(t.startswith("level L0 -> L") for t in rec.texts)
    assert rec.paused.is_set()  # starved host: annotator paused


def _restd(env):
    return {"Name": "/slurmrestd", "HostConfig": {"Memory": 1}, "Mounts": [],
            "Config": {"Env": ["PATH=/bin", *env]}}


def test_slurmrestd_accepted_options_pass_and_are_listed_as_exception():
    from governor.watchdog import accepted_exceptions, container_violations

    c = _restd(["SLURMRESTD_SECURITY=disable_unshare_sysv,disable_unshare_files"])
    assert container_violations([c]) == []
    assert accepted_exceptions([c]) == ["slurmrestd: slurmrestd skips its unshare steps (ADR 0004)"]
    assert accepted_exceptions([_restd([])]) == []


def test_slurmrestd_extra_options_are_violations():
    from governor.watchdog import container_violations

    for bad in ("disable_user_check", "become_user", "disable_unshare_sysv,disable_unshare_filez"):
        got = container_violations([_restd([f"SLURMRESTD_SECURITY={bad}"])])
        assert any("unaccepted options" in g for g in got), bad
    assert container_violations([{"Name": "/x", "HostConfig": {"Memory": 1}}]) == []  # no Config key at all


def _calm_gov(tmp_path, avail=4 * GB, effector=None, dry_run=False):
    import json

    from governor.signals import FileSampler
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"mem_available": int(avail), "psi_some": 0, "psi_full": 0}))
    g = Governor(FileSampler(f), Ladder(), [], lambda a: None, dry_run, effector=effector)
    g.step(0)
    return g, f


def test_admit_approves_when_calm_and_fits(tmp_path):
    g, _ = _calm_gov(tmp_path)
    assert g.handle("admit", {"budget_mb": 1000})["ok"]
    r = g.handle("admit", {"budget_mb": 3500})  # 4 GB - 3500 MB < 1 GB floor
    assert not r["ok"] and "floor" in r["error"]


def test_admit_refused_under_pressure(tmp_path):
    g, f = _calm_gov(tmp_path)
    import json
    f.write_text(json.dumps({"mem_available": 4 * GB, "psi_some": 25, "psi_full": 0}))
    g.step(1)
    g.step(11)  # L2 needs 10 s of sustained pressure
    assert g.ladder.level >= 2
    r = g.handle("admit", {"budget_mb": 1})
    assert not r["ok"] and "L" in r["error"]
    assert not g.handle("scale", {"group": "compute", "n": 3})["ok"]  # GV-8


def test_scale_and_set_profile_run_effector_only_when_calm(tmp_path):
    import threading
    calls, done = [], threading.Event()

    def eff(args):
        calls.append(args)
        done.set()
        return 0

    g, _ = _calm_gov(tmp_path, effector=eff)
    assert g.handle("scale", {"group": "compute", "n": 3})["started"]
    assert done.wait(5) and calls == [["scale", "3"]]
    assert not g.handle("scale", {"group": "gpu", "n": 1})["ok"]
    done.clear()
    g.handle("set-profile", {"profile": "min"})
    assert done.wait(5) and calls[-1] == ["up", "--profile", "min"]
    assert any("scale=3: done" in a for a in g.audit)


def test_scale_dry_run_does_not_execute(tmp_path):
    g, _ = _calm_gov(tmp_path, effector=lambda a: 1 / 0, dry_run=True)
    assert g.handle("scale", {"group": "compute", "n": 2}) == {"ok": True, "dry_run": True}


def test_control_validates_admit():
    from governor.control import dispatch
    assert not dispatch(b'{"cmd":"admit","budget_mb":"x"}', lambda c, r: {"ok": True}, set)["ok"]


def test_metrics_text_and_endpoint(tmp_path):
    import urllib.error
    import urllib.request

    from governor import metrics

    g, _ = _calm_gov(tmp_path)
    g.handle("admit", {"budget_mb": 100})
    g.handle("admit", {"budget_mb": 99999})
    g.step(1)
    text = g.metrics.decode()
    assert "workbench_governor_level 0" in text
    assert f"workbench_host_mem_available_bytes {4 * GB}" in text
    assert 'workbench_governor_admit_total{result="ok"} 1' in text
    assert 'workbench_governor_admit_total{result="refused"} 1' in text
    srv = metrics.serve("127.0.0.1:0", g)
    try:
        port = srv.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:
            assert r.status == 200 and b"workbench_governor_level" in r.read()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=5)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
