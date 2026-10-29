"""Edge cases of the governor's helpers: annotations, the control socket, the executor's verbs and refresh."""
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from governor import control
from governor.annotate import Annotator, http_post
from governor.executors import Executor, discover_docker
from governor.memlock import MitigationPath
from governor.planner import Action, Group


def wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_http_post_sends_a_bearer_json_annotation():
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.update(path=self.path, auth=self.headers["Authorization"], ctype=self.headers["Content-Type"],
                       body=json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        http_post(f"http://127.0.0.1:{srv.server_port}/", "tok", {"text": "L3"})
    finally:
        srv.shutdown()
    assert got == {"path": "/api/annotations", "auth": "Bearer tok", "ctype": "application/json", "body": {"text": "L3"}}


def test_annotator_sends_in_order_and_counts():
    sent = []
    a = Annotator("http://x", "t", post=lambda url, tok, item: sent.append(item["text"]))
    for t in ("a", "b", "c"):
        a.emit(t)
    assert wait(lambda: a.sent == 3)
    a.close()
    assert sent == ["a", "b", "c"] and not a.failed


def test_annotator_survives_failures_and_recovers():
    outcomes = iter([OSError("down"), OSError("down"), None])
    sent = []

    def post(url, tok, item):
        r = next(outcomes)
        if r:
            raise r
        sent.append(item["text"])

    a = Annotator("http://x", "t", post=post)
    a.emit("1")
    assert wait(lambda: a.failed)
    a.emit("2")
    a.emit("3")
    assert wait(lambda: not a.failed and a.sent == 1)  # third call succeeded; thread never died
    a.close()
    assert sent == ["3"]


def test_annotator_drops_when_full_and_holds_while_paused():
    sent = []
    a = Annotator("http://x", "t", post=lambda *args: sent.append(1))
    a.paused.set()
    time.sleep(0.7)  # let the worker settle into its paused wait
    for i in range(Annotator.MAX_QUEUE + 25):
        a.emit(str(i))
    assert a.q.qsize() == Annotator.MAX_QUEUE and sent == []  # extra events dropped, nothing sent while paused
    a.close()


def test_dispatch_survives_a_crashing_handler_and_rejects_oversize_and_non_objects():
    def boom(cmd, req):
        raise RuntimeError("kaboom")

    assert control.dispatch(b'{"cmd": "status"}', boom, lambda: set())["error"] == "internal: kaboom"
    big = b'{"cmd": "status", "pad": "' + b"x" * (control.MAX_REQUEST + 1) + b'"}'
    assert control.dispatch(big, boom, lambda: set())["error"] == "request too large"
    assert control.dispatch(b"[1, 2]", boom, lambda: set())["error"] == "request must be an object"
    assert "bad json" in control.dispatch(b"{nope", boom, lambda: set())["error"]
    assert control.dispatch(b'{"cmd": "scale", "group": "compute", "n": -1}', boom, lambda: {"compute"})["ok"] is False
    assert control.dispatch(b'{"cmd": "set-profile"}', boom, lambda: set())["error"] == "set-profile needs profile:str"
    assert control.dispatch(b'{"cmd": "admit", "budget_mb": "x"}', boom, lambda: set())["error"].startswith("admit needs")


def test_control_server_ignores_blank_lines_and_replaces_a_stale_socket(tmp_path):
    import socket
    path = tmp_path / "gov.sock"
    path.write_text("stale file from a crashed run")
    srv = control.serve(str(path), lambda cmd, req: {"ok": True, "cmd": cmd}, lambda: set())
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(path))
            s.sendall(b"\n")
            s.shutdown(socket.SHUT_WR)
            assert s.recv(100) == b""  # a blank request gets no reply and no crash
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(path))
            s.sendall(b'{"cmd": "status"}\n')
            assert json.loads(s.makefile().readline()) == {"ok": True, "cmd": "status"}
    finally:
        srv.shutdown()


def test_discover_docker_with_no_containers_is_empty():
    calls = []

    def run(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert discover_docker(run) == ([], {})
    assert len(calls) == 1  # no inspect without ids


def make_cg(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    for f in ("cgroup.freeze", "memory.high", "memory.reclaim", "cgroup.kill"):
        (d / f).write_text("")
    (d / "memory.current").write_text("1000")
    return d


def test_stop_start_and_unknown_verbs():
    calls = []

    def fake(cmd, timeout=15):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ex = Executor([Group("cn1", 4)], {}, MitigationPath(), run_cmd=fake)
    ex(Action("start", "cn1", 0))
    ex(Action("notify", "cn1", 1))  # no-op
    assert calls == [["docker", "start", "cn1"]]
    with pytest.raises(NotImplementedError):
        ex(Action("explode", "cn1", 5))


def test_failed_cli_raises_with_the_stderr():
    ex = Executor([Group("cn1", 4)], {}, MitigationPath(),
                  run_cmd=lambda cmd, timeout=15: subprocess.CompletedProcess(cmd, 1, "", "no such container"))
    with pytest.raises(RuntimeError, match="no such container"):
        ex(Action("start", "cn1", 0))


def test_stop_kills_the_cgroup_first_when_one_is_known(tmp_path):
    cg = make_cg(tmp_path, "cn1")
    calls = []
    ex = Executor([Group("cn1", 4)], {"cn1": cg}, MitigationPath(),
                  run_cmd=lambda cmd, timeout=15: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    ex(Action("stop", "cn1", 4))
    assert (cg / "cgroup.kill").read_text() == "1" and calls[0][:2] == ["docker", "stop"]


def test_refresh_closes_handles_of_vanished_cgroups_and_opens_new_ones(tmp_path):
    a, b = make_cg(tmp_path, "a"), make_cg(tmp_path, "b")
    path = MitigationPath()
    ex = Executor([Group("a", 4)], {"a": a}, path)
    assert path.has(str(a / "cgroup.freeze"))
    ex.refresh([Group("b", 4)], {"b": b})
    assert not path.has(str(a / "cgroup.freeze")) and path.has(str(b / "cgroup.freeze"))
    assert ex.kinds == {"b": "container"}


def test_unopenable_control_file_is_skipped_not_fatal(tmp_path, caplog):
    cg = make_cg(tmp_path, "a")
    (cg / "memory.high").unlink()
    (cg / "memory.high").mkdir()  # exists but cannot be opened for writing
    with caplog.at_level("WARNING", logger="governor.executors"):
        Executor([Group("a", 4)], {"a": cg}, MitigationPath())
    assert any("cannot open" in r.message for r in caplog.records)
