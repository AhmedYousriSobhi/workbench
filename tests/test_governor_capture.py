"""The governor's on-demand wire capture (issue 505): one at a time, L0-L1 only, bounded, and gone when nobody reads it."""
import io
import json
import threading
import time

import pytest

from governor import control
from governor.capture import MAX_SECONDS, RING, Capture


class FakeProc:
    """A `benchctl get capture` that prints the lines it is given, then waits to be terminated (or ends by itself)."""

    def __init__(self, lines, end_rc=None, stderr=""):
        self.lines, self.rc, self.terminated = list(lines), end_rc, threading.Event()
        self.stderr = io.StringIO(stderr)
        self.stdout = self._gen()

    def _gen(self):
        for ln in self.lines:
            yield ln if ln.endswith("\n") else ln + "\n"
        if self.rc is None:
            self.terminated.wait(5)

    def terminate(self):
        self.terminated.set()

    def wait(self):
        return self.rc if self.rc is not None else -15


def msg(i):
    return json.dumps({"t": float(i), "dir": "in", "type": "REQUEST_PING", "kind": "request", "size": 30})


def make(lines=(), level=0, **kw):
    log, clock = [], {"t": 0.0}
    procs = []

    def spawn(node, seconds):
        procs.append((node, seconds, FakeProc(lines, **kw)))
        return procs[-1][2]
    cap = Capture(spawn, lambda: level_box[0], log.append, clock=lambda: clock["t"], tick=0.01)
    level_box = [level]
    return cap, log, clock, level_box, procs


def settle(cap, n):
    deadline = time.time() + 3
    while cap.read()["next"] < n and time.time() < deadline:
        time.sleep(0.01)


def test_a_capture_starts_reads_what_arrives_and_stops():
    cap, log, _clock, _lvl, procs = make([msg(1), msg(2), msg(3)])
    assert cap.start("cn001") == {"ok": True, "node": "cn001", "seconds": MAX_SECONDS} and procs[0][:2] == ("cn001", MAX_SECONDS)
    settle(cap, 3)
    r = cap.read(0)
    assert [m["t"] for m in r["messages"]] == [1.0, 2.0, 3.0] and r["next"] == 3 and r["running"] and r["class"] == "P5" and r["seconds_left"] == MAX_SECONDS
    assert cap.read(2)["messages"][0]["t"] == 3.0 and cap.read(3)["messages"] == []  # only what is new
    assert cap.stop("stopped by the page")["running"] is False and procs[0][2].terminated.is_set()
    assert cap.read(0)["ended"] == "stopped by the page" and not cap.read()["running"]
    assert any("capture start cn001 (class P5" in x for x in log) and any("capture stop cn001" in x for x in log)


def test_one_wire_at_a_time():
    cap, _log, _clock, _lvl, procs = make([msg(1)])
    cap.start("cn001")
    assert cap.start("cn001") == {"ok": True, "node": "cn001", "already": True}  # the same wire again is the same capture
    other = cap.start("cn002")
    assert other["ok"] is False and "one wire at a time" in other["error"] and len(procs) == 1
    cap.stop()


@pytest.mark.parametrize("level,ok", [(0, True), (1, True), (2, False), (3, False), (5, False)])
def test_it_starts_only_at_l0_and_l1(level, ok):
    cap, _log, _clock, _lvl, procs = make(level=level)
    r = cap.start("cn001")
    assert r["ok"] is ok and bool(procs) is ok
    if not ok:
        assert "memory pressure" in r["error"] and f"L{level}" in r["error"]
    cap.stop()


def wait_ended(cap, timeout=3):
    end = time.time() + timeout
    while cap.running() and time.time() < end:  # not read(): reading keeps a capture alive
        time.sleep(0.01)
    return cap.read()


def test_it_stops_itself_when_the_ladder_rises():
    cap, log, _clock, lvl, procs = make([msg(1)])
    cap.start("cn001")
    lvl[0] = 2
    r = wait_ended(cap)
    assert not r["running"] and "memory pressure" in r["ended"] and procs[0][2].terminated.is_set() and any("memory pressure" in x for x in log)


def test_it_stops_after_five_minutes_and_when_the_page_stops_asking():
    cap, _log, clock, _lvl, _procs = make([msg(1)])
    cap.start("cn001")
    clock["t"] = MAX_SECONDS + 1
    assert wait_ended(cap)["ended"] == "time limit"
    cap2, _l, clock2, _v, procs2 = make([msg(1)])
    cap2.start("cn001")
    clock2["t"] = 20  # nobody read for more than 15 s: a closed tab
    r = wait_ended(cap2)
    assert r["ended"] == "the page stopped asking" and procs2[0][2].terminated.is_set()
    cap3, _l3, clock3, _v3, _p3 = make([msg(1)])
    cap3.start("cn001")
    for t in range(1, 40, 10):  # a page that keeps reading keeps it alive
        clock3["t"] = t
        cap3.read()
        time.sleep(0.05)
    assert cap3.read()["running"]
    cap3.stop()


def test_the_ring_is_bounded_and_a_slow_reader_gets_what_is_left():
    cap, _log, _clock, _lvl, _procs = make([msg(i) for i in range(RING + 100)])
    cap.start("cn001")
    settle(cap, RING + 100)
    r = cap.read(0)
    assert len(r["messages"]) == RING and r["messages"][0]["t"] == 100.0 and r["next"] == RING + 100
    cap.stop()


def test_a_failed_capture_says_why_and_junk_lines_are_ignored():
    cap, log, _clock, _lvl, _procs = make(["not json", "[1, 2]", msg(1)], end_rc=1, stderr="capture failed: tcpdump in workbench-cn001-1 exited 1: permission denied")
    cap.start("cn001")
    r = wait_ended(cap)
    assert r["ended"] == "failed" and "permission denied" in r["error"] and [m["t"] for m in r["messages"]] == [1.0]
    assert any("failed" in x and "permission denied" in x for x in log)


def test_a_new_capture_forgets_the_last_one_and_nothing_is_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cap, _log, _clock, _lvl, _procs = make([msg(1)], end_rc=0)
    cap.start("cn001")
    settle(cap, 1)
    wait_ended(cap)
    cap.start("cn002")
    settle(cap, 1)
    assert cap.read(0)["node"] == "cn002" and cap.read(0)["next"] == 1
    assert list(tmp_path.iterdir()) == []


def test_a_spawn_that_fails_is_a_clear_refusal():
    cap = Capture(lambda n, s: (_ for _ in ()).throw(OSError("no benchctl")), lambda: 0, lambda m: None)
    assert cap.start("cn001") == {"ok": False, "error": "capture could not start: no benchctl"}


def ask(handle, req):
    return control.dispatch(json.dumps(req).encode(), handle, lambda: set())


def test_the_control_socket_validates_the_capture_commands():
    seen = []
    handle = lambda cmd, req: seen.append((cmd, req)) or {"ok": True}
    assert ask(handle, {"cmd": "capture-start", "node": "cn001"}) == {"ok": True}
    for bad in ({"cmd": "capture-start"}, {"cmd": "capture-start", "node": "cn001; rm -rf"}, {"cmd": "capture-start", "node": "../x"}, {"cmd": "capture-start", "node": 5},
                {"cmd": "capture-read", "since": -1}, {"cmd": "capture-read", "since": "0"}, {"cmd": "capture-read", "since": True}):
        assert ask(handle, bad)["ok"] is False, bad
    assert ask(handle, {"cmd": "capture-read"}) == {"ok": True} and ask(handle, {"cmd": "capture-read", "since": 12}) == {"ok": True} and ask(handle, {"cmd": "capture-stop"}) == {"ok": True}
    assert [c for c, _ in seen] == ["capture-start", "capture-read", "capture-read", "capture-stop"]
