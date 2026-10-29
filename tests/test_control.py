import json
import os
import socket
import tempfile
from pathlib import Path

from governor import control
from governor.core import Governor
from governor.ladder import Ladder
from governor.memlock import MitigationPath
from governor.planner import Group
from governor.signals import GB, Signals


class FakeSampler:
    def __init__(self):
        self.s = Signals(4 * GB, 0, 0)

    def sample(self, now):
        return self.s


def make(dry=True, executed=None):
    ex = (lambda a: executed.append(a)) if executed is not None else (lambda a: None)
    return Governor(FakeSampler(), Ladder(), [Group("cn1", 4), Group("vm", 6, "vm")], ex, dry)


def ask(path, payload: bytes) -> dict:
    with socket.socket(socket.AF_UNIX) as c:
        c.connect(path)
        c.sendall(payload)
        return json.loads(c.makefile().readline())


def test_dispatch_rejects_unknown_and_bad():
    g = make()
    d = lambda raw: control.dispatch(raw, g.handle, g.group_names)
    assert d(b'{"cmd":"rm-rf"}')["ok"] is False
    assert d(b"not json")["ok"] is False
    assert d(b"[1]")["ok"] is False
    assert d(b'{"cmd":"freeze","target":"nope"}')["error"].startswith("unknown group")
    assert d(b'{"cmd":"scale","group":"cn","n":-1}')["ok"] is False
    assert d(b'{"cmd":"set-profile"}')["ok"] is False
    assert d(b"x" * 5000)["error"] == "request too large"


def test_socket_roundtrip_and_audit():
    g = make()
    g.step(0)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "gov.sock")
        srv = control.serve(p, g.handle, g.group_names)
        try:
            assert oct(os.stat(p).st_mode & 0o777) == "0o660"
            st = ask(p, b'{"cmd":"status"}\n')
            assert st["ok"] and st["level"] == 0 and "cn1" in st["groups"]
            assert ask(p, b'{"cmd":"freeze","target":"cn1"}\n')["ok"]
            assert "dry-run: would L0 freeze cn1" in ask(p, b'{"cmd":"status"}\n')["audit"]
            assert ask(p, b'{"cmd":"bogus"}\n')["ok"] is False
        finally:
            srv.shutdown()


def test_real_mode_calls_executor():
    done = []
    g = make(dry=False, executed=done)
    g.handle("stop", {"target": "vm"})
    assert [str(a) for a in done] == ["L0 stop vm"]


def test_level_change_runs_plan():
    g = make()
    g.sampler.s = Signals(4 * GB, 0, 6.0)  # full PSI -> L4
    assert g.step(0) == 4
    assert any("save vm" in m for m in g.audit)


def test_mitigation_path_preopened():
    with tempfile.NamedTemporaryFile() as f:
        mp = MitigationPath(1024)
        mp.open(f.name)
        mp.write(f.name, b"x")
        mp.close()
        assert Path(f.name).read_bytes() == b"x"


def test_socket_dir_follows_socket_gid():
    g = make()
    with tempfile.TemporaryDirectory() as d:
        run = os.path.join(d, "run")
        srv = control.serve(os.path.join(run, "gov.sock"), g.handle, g.group_names,
                            gid=os.getgid())
        try:
            st = os.stat(run)
            assert st.st_gid == os.getgid() and st.st_mode & 0o777 == 0o750
        finally:
            srv.shutdown()
