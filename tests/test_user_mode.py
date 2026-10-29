"""Rootless governor (ADR 0010): the Docker API replaces cgroup files, nothing needs sudo."""
import json
import logging
import subprocess

import pytest

from governor import __main__ as gm
from governor.core import Governor
from governor.executors import Executor, UserExecutor
from governor.planner import Action, Group
from simctl import checks


class Fake:
    def __init__(self, rc=0, err=""):
        self.calls, self.rc, self.err = [], rc, err

    def __call__(self, cmd, timeout=15):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, "", self.err)


def ex(run, tmp_path):
    return UserExecutor([Group("web", 4)], {}, run_cmd=run, arrivals_flag=str(tmp_path / "arrivals"))


def test_freeze_thaw_stop_go_through_docker(tmp_path):
    run = Fake()
    e = ex(run, tmp_path)
    for verb in ("freeze", "thaw", "stop"):
        e(Action(verb, "web", 3))
    assert run.calls == [["docker", "pause", "web"], ["docker", "unpause", "web"], ["docker", "stop", "-t", "2", "web"]]


def test_already_paused_is_fine_other_failures_are_not(tmp_path):
    ex(Fake(1, "Error: container web is already paused"), tmp_path)(Action("freeze", "web", 3))
    ex(Fake(1, "Error: container web is not paused"), tmp_path)(Action("thaw", "web", 3))
    with pytest.raises(RuntimeError, match="no such container"):
        ex(Fake(1, "Error: no such container"), tmp_path)(Action("freeze", "web", 3))


def test_root_only_verbs_are_skipped_once_each(tmp_path, caplog):
    run = Fake()
    e = ex(run, tmp_path)
    with caplog.at_level(logging.INFO, logger="governor.exec"):
        for _ in range(3):
            for verb in ("throttle=1", "unthrottle", "reclaim"):
                e(Action(verb, "web", 2))
    assert run.calls == []
    assert len([r for r in caplog.records if "needs root" in r.message]) == 3


def test_arrival_flag_and_start_still_work(tmp_path):
    run = Fake()
    e = ex(run, tmp_path)
    e(Action("stop-arrivals", "*", 1))
    assert (tmp_path / "arrivals").exists()
    e(Action("resume-arrivals", "*", 0))
    assert not (tmp_path / "arrivals").exists()
    e(Action("start", "web", 0))
    assert run.calls == [["docker", "start", "web"]]


def test_prealloc_opens_no_cgroup_files(tmp_path):
    cg = tmp_path / "cg"
    cg.mkdir()
    (cg / "cgroup.freeze").write_text("0")
    e = UserExecutor([Group("web", 4)], {"web": cg}, run_cmd=Fake())
    assert not e.path.has(str(cg / "cgroup.freeze"))
    assert Executor.__mro__[0] is Executor  # the root executor is unchanged


def test_status_reports_the_mode(tmp_path):
    from governor.ladder import Ladder
    from governor.signals import FileSampler
    sig = tmp_path / "s.json"
    sig.write_text(json.dumps({"mem_available": 8 * 2**30, "psi_some": 0.0, "psi_full": 0.0}))
    for cls, mode in ((UserExecutor, "user"), (Executor, "root")):
        gov = Governor(FileSampler(sig), Ladder(), [Group("web", 4)], cls([Group("web", 4)], {}, run_cmd=Fake()), True)
        assert gov.handle("status", {})["mode"] == mode


def test_main_user_mode_runs_without_root(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(gm, "discover_docker", lambda: ([Group("web", 4, "docker")], {}))
    monkeypatch.setattr(gm, "lock_memory", lambda: (_ for _ in ()).throw(OSError("EPERM")))  # what a user gets
    monkeypatch.setattr(gm.signal, "signal", lambda *a: None)
    sig = tmp_path / "sig.json"
    sig.write_text(json.dumps({"mem_available": 8 * 2**30, "psi_some": 0.0, "psi_full": 0.0}))
    with caplog.at_level(logging.INFO, logger="governor"):
        gm.main(["--user-mode", "--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig)])
    assert any("mlockall failed" in r.message and r.levelno == logging.INFO for r in caplog.records)  # not an error


def test_default_socket_prefers_env_then_system_then_user():
    sysp, usr = checks.SYSTEM_SOCKET, checks.user_socket()
    assert checks.default_socket({"HPCSIM_GOV_SOCKET": "/x/y.sock"}, lambda p: True) == "/x/y.sock"
    assert checks.default_socket({}, lambda p: True) == sysp  # both exist: the system service wins
    assert checks.default_socket({}, lambda p: p == usr) == usr  # only the rootless governor is there
    assert checks.default_socket({}, lambda p: False) == sysp  # nothing yet: the documented default


def test_user_socket_lives_under_the_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")
    assert checks.user_socket() == "/run/user/4242/hpcsim/gov.sock"
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert checks.user_socket().startswith("/tmp/hpcsim-") and checks.user_socket().endswith("/hpcsim/gov.sock")
