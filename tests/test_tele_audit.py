"""Teleport audit-log helpers (issue 754): parse, select, wait, and a clear error when the folder is missing."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from benchctl import teleportaudit as ta

LINES = """\
{"code":"T3009I","event":"kube.request","time":"2026-11-17T10:00:00.000Z","user":"alice","kubernetes_cluster":"workbench-k8s","verb":"GET","request_path":"/api/v1/nodes/kcn001","resource_kind":"nodes","resource_name":"kcn001","response_code":200}
{"code":"T3009I","event":"kube.request","time":"2026-11-17T10:00:05.000Z","user":"alice","kubernetes_cluster":"workbench-k8s","verb":"DELETE","request_path":"/api/v1/namespaces/team-a/pods/p","response_code":403}
{"event":"session.start","time":"2026-11-17T10:00:06Z","user":"bob"}
{"event":"kube.request","time":"not a time","user":"carol","response_code":"200"}
{"partial":
not json"""


def test_parse_skips_partial_and_non_json_lines():
    assert [e["event"] for e in ta.parse(LINES)] == ["kube.request", "kube.request", "session.start", "kube.request"]


def test_select_by_fields_since_and_number_or_string_codes():
    ev = ta.parse(LINES)
    assert [e["verb"] for e in ta.select(ev, user="alice", response_code=200)] == ["GET"]
    assert [e["verb"] for e in ta.select(ev, user="alice", response_code=403)] == ["DELETE"]
    assert ta.select(ev, user="carol", response_code=200)  # a code written as a string still matches
    late = datetime(2026, 11, 17, 10, 0, 3, tzinfo=UTC)
    assert [e["verb"] for e in ta.select(ev, user="alice", since=late)] == ["DELETE"]
    assert ta.select(ev, event="session.start")[0]["user"] == "bob"
    assert ta.types_seen(ev) == ["kube.request", "session.start"]


def test_a_missing_folder_is_an_error_naming_it_not_an_empty_log():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=1, stdout="", stderr="")
    with pytest.raises(ta.AuditError, match="/var/lib/teleport/log does not exist"):
        ta.read_log(runner)
    assert calls[0][:3] == ["docker", "exec", ta.CONTAINER]


def test_read_log_returns_the_files_text_and_reports_a_read_failure():
    ok = [SimpleNamespace(returncode=0, stdout="", stderr=""), SimpleNamespace(returncode=0, stdout=LINES, stderr="")]
    assert ta.read_log(lambda *_a, **_k: ok.pop(0)) == LINES
    bad = [SimpleNamespace(returncode=0, stdout="", stderr=""), SimpleNamespace(returncode=1, stdout="", stderr="denied")]
    with pytest.raises(ta.AuditError, match="denied"):
        ta.read_log(lambda *_a, **_k: bad.pop(0))


def test_wait_returns_when_events_appear_and_gives_up_at_the_timeout():
    t = [0.0]
    answers = iter([[], [], [{"event": "kube.request"}]])
    got = ta.wait_for_events(lambda: next(answers), timeout=10, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert got == [{"event": "kube.request"}]
    t[0] = 0.0
    assert ta.wait_for_events(list, timeout=3, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0]) == []
