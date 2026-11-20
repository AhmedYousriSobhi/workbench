"""The per-user Kubernetes API request counter (issue 384, ADR 0087): the audit parser, the governor verb and the page's counting."""
import json

from benchctl import kubeaudit
from governor import control
from governor.core import Governor
from governor.ladder import Ladder
from governor.signals import FileSampler
from webui.flow import Flow
from webui.kubeaudit import KEEP, SCOPE, KubeAudit

GB = 1024 ** 3


def ev(i, user="viewer", t="2026-11-19T10:00:00.000000Z", res="pods", imp=None, stage="ResponseComplete", code=200, **extra):
    e = {"auditID": f"a{i}", "stage": stage, "verb": "list", "user": {"username": user}, "stageTimestamp": t,
         "objectRef": {"resource": res, "namespace": "team-a", "name": "p1"}, "responseStatus": {"code": code}, **extra}
    if imp:
        e["impersonatedUser"] = {"username": imp, "groups": ["workbench-k8s-viewers"]}
    return e


T0 = 1795082400.0  # 2026-11-19T10:00:00Z


# ---- the parser -------------------------------------------------------------------------------------------------------------
def test_compact_keeps_who_verb_object_and_answer_and_never_a_body():
    c = kubeaudit.compact(ev(1, requestObject={"secret": "x"}, responseObject={"secret": "y"}))
    assert c == {"id": "a1", "t": T0, "user": "viewer", "verb": "list", "resource": "pods", "object": "team-a/p1", "code": 200}
    assert "x" not in json.dumps(c) and "secret" not in json.dumps(c)


def test_a_teleport_request_counts_for_the_impersonated_user():
    assert kubeaudit.compact(ev(1, user="teleport", imp="alice"))["user"] == "alice"


def test_only_finished_requests_with_a_user_count():
    assert kubeaudit.compact(ev(1, stage="ResponseStarted")) is None
    assert kubeaudit.compact({"auditID": "z", "stage": "ResponseComplete"}) is None
    assert kubeaudit.compact(ev(1, user=""))is None


def test_subresources_and_cluster_scoped_objects():
    e = ev(1, res="nodes")
    e["objectRef"] = {"resource": "nodes", "subresource": "status", "name": "kn001"}
    c = kubeaudit.compact(e)
    assert c["resource"] == "nodes/status" and c["object"] == "kn001"


def test_read_is_bounded_deduplicated_sorted_and_looks_back_at_most_ten_minutes():
    asked = []
    many = [ev(i, t=f"2026-11-19T10:{i // 60:02d}:{i % 60:02d}.000000Z") for i in range(700)] + [ev(650)]  # a repeated id
    out = kubeaudit.read(0, T0 + 700, logs=lambda s: asked.append(s) or many)
    assert asked == [T0 + 700 - kubeaudit.MAX_WINDOW_S]  # since=0 is clamped to ten minutes back
    assert len(out) == kubeaudit.MAX_EVENTS == 500 and [c["t"] for c in out] == sorted(c["t"] for c in out)
    assert len({c["id"] for c in out}) == len(out)
    assert out[-1]["id"] == "a699"  # the newest are kept


# ---- the governor verb ------------------------------------------------------------------------------------------------------
def gov(tmp_path, level=0, dry_run=False):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"mem_available": 4 * GB, "psi_some": 0, "psi_full": 0}))
    g = Governor(FileSampler(f), Ladder(), [], lambda a: None, dry_run)
    g.step(0)
    g.ladder.level = level
    return g


def test_the_verb_needs_a_reader_refuses_a_dry_run_and_is_bounded(tmp_path):
    g = gov(tmp_path)
    assert "no Kubernetes audit without --workdir" in g.handle("kube-audit", {})["error"]
    asked = []
    g.reader = lambda argv: asked.append(argv) or json.dumps([{"id": str(i), "t": float(i), "user": "viewer"} for i in range(900)])
    got = g.handle("kube-audit", {"since": 12.5})
    assert got["ok"] and got["class"] == "P5" and len(got["events"]) == 500
    assert asked == [["get", "kube-audit", "--since", "12.5"]]
    g.reader = lambda argv: 1 / 0
    assert "kube audit failed" in g.handle("kube-audit", {})["error"]
    d = gov(tmp_path, dry_run=True)
    d.reader = lambda argv: "[]"
    assert "dry run" in d.handle("kube-audit", {})["error"]


def test_the_verb_is_refused_at_l2_and_above_before_anything_runs(tmp_path):
    asked = []
    for lvl, ok in ((0, True), (1, True), (2, False), (3, False), (4, False)):
        g = gov(tmp_path, lvl)
        g.reader = lambda argv: asked.append(argv) or "[]"
        r = g.handle("kube-audit", {})
        assert r["ok"] is ok
        if not ok:
            assert "L0-L1" in r["error"] and "memory pressure" in r["error"]
    assert len(asked) == 2  # only the L0 and L1 calls reached the reader


def test_the_socket_checks_since():
    d = lambda r: control.dispatch(json.dumps(r).encode(), lambda c, q: {"ok": True}, lambda: {"compute"})
    assert d({"cmd": "kube-audit"})["ok"] and d({"cmd": "kube-audit", "since": 1763500000.5})["ok"]
    for bad in (-1, "now", True, None, [1]):
        assert d({"cmd": "kube-audit", "since": bad})["ok"] is False


# ---- the page's counting ----------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def rec(i, user, t):
    return {"id": f"r{i}", "t": t, "user": user, "verb": "list", "resource": "pods", "object": None, "code": 200}


def test_it_counts_per_user_over_the_last_minute_and_lists_the_latest_first():
    clock, sent = Clock(), []
    answers = [{"ok": True, "events": [rec(1, "viewer", T0 - 70), rec(2, "viewer", T0 - 10), rec(3, "alice", T0 - 5), rec(4, "viewer", T0 - 1)]}]
    k = KubeAudit(lambda req: sent.append(req) or answers[0], clock)
    k.poll()
    s = k.summary()
    assert s["measured"] and s["scope"] == SCOPE == "nodes and pods only"
    assert s["users"] == [{"user": "viewer", "per_min": 2}, {"user": "alice", "per_min": 1}]  # the 70 s old one is out of the minute
    assert [e["user"] for e in s["recent"]][:2] == ["viewer", "alice"] and s["recent"][0]["t"] == T0 - 1
    assert sent[0]["cmd"] == "kube-audit"
    clock.t += 1
    k.poll()
    assert len(sent) == 1  # asked at most every 5 s, however many tabs
    clock.t += 5
    answers[0] = {"ok": True, "events": [rec(4, "viewer", T0 - 1), rec(5, "bob", T0 + 5)]}  # r4 again: not counted twice
    k.poll()
    assert {u["user"]: u["per_min"] for u in k.summary()["users"]} == {"viewer": 2, "alice": 1, "bob": 1}
    assert sent[1]["since"] >= T0 - 1 - 2.1  # asked from the newest record, not from the start


def test_memory_is_bounded():
    k = KubeAudit(lambda req: {"ok": True, "events": [rec(i, "u", T0) for i in range(KEEP + 300)]}, lambda: T0)
    k.poll()
    assert len(k.events) == KEEP and len(k.summary()["recent"]) <= 20


def test_a_refusal_is_not_measured_never_zero_and_is_retried_later():
    clock, calls = Clock(), []
    answer = {"ok": False, "error": "paused: memory pressure (L2); the Kubernetes request counter runs only at L0-L1"}
    k = KubeAudit(lambda req: calls.append(1) or answer, clock)
    k.poll()
    s = k.summary()
    assert s["measured"] is False and "L2" in s["note"] and s["users"] == [] and s["scope"] == SCOPE
    clock.t += 5
    k.poll()
    assert len(calls) == 1  # backed off
    clock.t += 15
    answer = {"ok": True, "events": []}
    k.poll()
    assert k.summary()["measured"] and k.summary()["users"] == []  # measured and quiet: an empty list, which the page words as "none"


def test_an_unreachable_governor_is_not_measured():
    def boom(req):
        raise OSError("no socket")
    k = KubeAudit(boom, lambda: T0)
    k.poll()
    assert k.summary()["measured"] is False and "not reachable" in k.summary()["note"]


def test_the_flow_snapshot_carries_it_only_while_the_api_answers(monkeypatch):
    from benchctl import k8s
    monkeypatch.setattr(k8s, "api_up", lambda *a, **kw: True)
    monkeypatch.setattr(k8s, "api_requests", lambda: 5)
    k = KubeAudit(lambda req: {"ok": True, "events": [rec(1, "viewer", T0)]}, lambda: T0)
    f = Flow(fetch=lambda *a: {"requests": 0}, min_interval=0, kube=k)
    f.poll()
    assert f.since(0)["kube"]["users"] == [{"user": "viewer", "per_min": 1}]
    monkeypatch.setattr(k8s, "api_up", lambda *a, **kw: False)
    f.poll(force=True)
    assert "kube" not in f.since(0)
    assert "kube" not in Flow(fetch=lambda *a: {"requests": 0}, min_interval=0).since(0)
