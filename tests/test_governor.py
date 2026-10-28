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
