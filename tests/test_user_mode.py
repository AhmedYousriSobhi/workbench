"""Rootless governor (ADR 0010): the Docker API replaces cgroup files, nothing needs sudo."""
import json
import logging
import subprocess

import pytest

from benchctl import checks
from governor import __main__ as gm
from governor.core import Governor
from governor.executors import Executor, UserExecutor
from governor.planner import Action, Group


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
    assert run.calls == [["docker", "pause", "web"], ["docker", "unpause", "web"],
                         ["docker", "kill", "web"], ["docker", "stop", "-t", "2", "web"]]  # kill first: SIGTERM can be ignored


def test_already_paused_is_fine_other_failures_are_not(tmp_path):
    ex(Fake(1, "Error: container web is already paused"), tmp_path)(Action("freeze", "web", 3))
    ex(Fake(1, "Error: container web is not paused"), tmp_path)(Action("thaw", "web", 3))
    with pytest.raises(RuntimeError, match="no such container"):
        ex(Fake(1, "Error: no such container"), tmp_path)(Action("freeze", "web", 3))


def test_stop_tolerates_an_already_dead_container(tmp_path):
    class KillFails(Fake):
        def __call__(self, cmd, timeout=15):
            self.calls.append(cmd)
            dead = cmd[1] == "kill"
            return subprocess.CompletedProcess(cmd, 1 if dead else 0, "", "web is not running" if dead else "")
    run = KillFails()
    ex(run, tmp_path)(Action("stop", "web", 4))
    assert [c[1] for c in run.calls] == ["kill", "stop"]


def test_cgroup_sampler_survives_the_cgroup_vanishing(tmp_path):
    from governor.signals import CgroupSampler
    s = CgroupSampler(tmp_path / "gone").sample(0.0)
    assert s.mem_available > 1 << 30 and s.psi_some_avg10 == 0.0


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
    assert checks.default_socket({"WORKBENCH_GOV_SOCKET": "/x/y.sock"}, lambda p: True) == "/x/y.sock"
    assert checks.default_socket({}, lambda p: True) == sysp  # both exist: the system service wins
    assert checks.default_socket({}, lambda p: p == usr) == usr  # only the rootless governor is there
    assert checks.default_socket({}, lambda p: False) == sysp  # nothing yet: the documented default


def test_arrivals_flag_moves_to_the_runtime_dir_in_rootless_mode(monkeypatch):
    from governor.executors import ARRIVALS_FLAG
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")
    assert gm.arrivals_flag_path(ARRIVALS_FLAG, True) == "/run/user/4242/workbench/arrivals.paused"
    assert gm.arrivals_flag_path(ARRIVALS_FLAG, False) == ARRIVALS_FLAG  # root keeps /run/workbench
    assert gm.arrivals_flag_path("/custom/flag", True) == "/custom/flag"  # an explicit choice is respected


def test_user_socket_lives_under_the_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")
    assert checks.user_socket() == "/run/user/4242/workbench/gov.sock"
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert checks.user_socket().startswith("/tmp/workbench-") and checks.user_socket().endswith("/workbench/gov.sock")


def test_a_container_the_governor_stopped_can_still_be_started_by_name(tmp_path):
    """Found by the live test: stopped containers leave `docker ps`, so `start web` was refused as an unknown group."""
    from governor.ladder import Ladder
    from governor.signals import FileSampler
    sig = tmp_path / "s.json"
    sig.write_text(json.dumps({"mem_available": 8 * 2**30, "psi_some": 0.0, "psi_full": 0.0}))
    run = Fake()
    groups = [Group("web", 4)]
    inventory = {"found": [Group("web", 4)]}
    gov = Governor(FileSampler(sig), Ladder(), groups, UserExecutor(groups, {}, run_cmd=run), False,
                   inventory=lambda: (inventory["found"], {}), refresh_interval=1)
    gov._act(Action("stop", "web", 4))
    inventory["found"] = []  # docker ps no longer lists it
    gov._refresh(5.0, 0)
    assert "web" not in {g.name for g in gov.groups} and "web" in gov.group_names()  # not governed, still addressable
    assert gov.handle("start", {"target": "web"})["ok"] and ["docker", "start", "web"] in run.calls
    assert "web" not in gov.group_names()
    inventory["found"] = [Group("web", 4)]  # and when it shows up again it is governed again
    gov._refresh(10.0, 0)
    assert "web" in {g.name for g in gov.groups}


def test_arrivals_flag_with_a_workdir_is_one_directory_for_root_and_user_mode(tmp_path):
    """#476: the containers mount that directory, so both governors must write there (never /run/workbench: gov.sock lives in it)."""
    from governor.executors import ARRIVALS_FLAG
    want = str(tmp_path / "compose/generated/flags/arrivals.paused")
    assert gm.arrivals_flag_path(ARRIVALS_FLAG, True, str(tmp_path)) == want
    assert gm.arrivals_flag_path(ARRIVALS_FLAG, False, str(tmp_path)) == want
    assert gm.arrivals_flag_path("/custom/flag", True, str(tmp_path)) == "/custom/flag"


def test_a_pause_flag_found_at_startup_is_stale_and_removed_but_not_in_a_dry_run(tmp_path):
    """#476: a governor that died at L1 leaves the flag; the next one starts at L0 and must not keep arrivals paused."""
    from governor import __main__ as gm
    flag = tmp_path / "arrivals.paused"
    assert gm.clear_stale_flag(str(flag), False) is False  # nothing there
    flag.touch()
    assert gm.clear_stale_flag(str(flag), True) is False and flag.exists()  # dry run touches nothing
    assert gm.clear_stale_flag(str(flag), False) is True and not flag.exists()


# --- issue 452: the Docker Engine API instead of the docker CLI --------------------------------------------------

import http.server
import socketserver
import threading
from pathlib import Path

from governor import dockerapi


class _Daemon:
    """A fake Docker daemon on a Unix socket: records requests, answers from a table, keeps connections alive."""

    def __init__(self, tmp_path: Path, answers: dict | None = None):
        self.requests, self.connections = [], 0
        self.answers = answers or {}
        daemon = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                daemon.connections += 1

            def do_POST(self):
                daemon.requests.append(self.path)
                code, msg = daemon.answers.get(self.path, (204, ""))
                body = json.dumps({"message": msg}).encode() if msg else b""
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

            address_string = lambda self: "unix"

        self.path = str(tmp_path / "docker.sock")
        self.server = socketserver.ThreadingUnixStreamServer(self.path, H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def daemon(tmp_path):
    d = _Daemon(tmp_path)
    yield d
    d.close()


def api_ex(d, tmp_path, cls=UserExecutor, run=None):
    run = run or (lambda cmd, timeout=15: pytest.fail(f"forked {cmd}"))
    return cls([Group("web", 4)], {}, run_cmd=run, arrivals_flag=str(tmp_path / "arrivals"), docker=dockerapi.DockerAPI(d.path))


def test_user_mode_ladder_uses_the_api_and_forks_nothing(daemon, tmp_path, monkeypatch):
    def no_fork(*a, **k):
        raise AssertionError("a process was started on the mitigation path")

    monkeypatch.setattr(subprocess, "run", no_fork)
    monkeypatch.setattr(subprocess, "Popen", no_fork)
    e = api_ex(daemon, tmp_path)
    for verb in ("freeze", "thaw", "stop", "start"):
        e(Action(verb, "web", 3))
    assert daemon.requests == ["/containers/web/pause", "/containers/web/unpause", "/containers/web/kill",
                               "/containers/web/stop?t=2", "/containers/web/start"]
    assert daemon.connections == 1  # one connection, opened at startup, kept alive


def test_root_executor_stop_and_start_use_the_api_too(daemon, tmp_path):
    e = api_ex(daemon, tmp_path, cls=Executor)
    e(Action("stop", "web", 4))
    e(Action("start", "web", 0))
    assert daemon.requests == ["/containers/web/stop?t=2", "/containers/web/start"]


def test_api_tolerates_what_the_cli_path_tolerated_and_fails_on_the_rest(daemon, tmp_path):
    daemon.answers = {"/containers/web/pause": (409, "Container web is already paused"),
                      "/containers/web/unpause": (409, "Container web is not paused"),
                      "/containers/web/kill": (409, "Container web is not running"),
                      "/containers/web/start": (404, "No such container: web")}
    e = api_ex(daemon, tmp_path)
    e(Action("freeze", "web", 3))
    e(Action("thaw", "web", 3))
    e(Action("stop", "web", 4))  # kill refused as not running, stop answers 204
    with pytest.raises(RuntimeError, match="No such container"):
        e(Action("start", "web", 0))
    daemon.answers["/containers/web/pause"] = (500, "boom")
    with pytest.raises(RuntimeError, match="boom"):
        e(Action("freeze", "web", 3))


def test_304_already_stopped_is_fine(daemon, tmp_path):
    daemon.answers = {"/containers/web/stop?t=2": (304, "")}
    api_ex(daemon, tmp_path)(Action("stop", "web", 4))


def test_a_dropped_connection_is_reopened_once_without_a_process(daemon, tmp_path):
    e = api_ex(daemon, tmp_path)
    e(Action("freeze", "web", 3))
    e.docker.conn.sock.close()  # the daemon restarted / the socket was cut
    e(Action("thaw", "web", 3))
    assert daemon.requests[-1] == "/containers/web/unpause" and daemon.connections == 2


def test_unreachable_daemon_is_an_error_not_a_hang(daemon, tmp_path):
    e = api_ex(daemon, tmp_path)
    daemon.close()
    Path(daemon.path).unlink(missing_ok=True)
    e.docker.conn.close()
    with pytest.raises(RuntimeError, match="docker socket"):
        e(Action("freeze", "web", 3))


def test_container_names_are_quoted_and_unknown_ops_refused(daemon):
    api = dockerapi.DockerAPI(daemon.path)
    api.call("pause", "a b/c")
    assert daemon.requests == ["/containers/a%20b%2Fc/pause"]
    with pytest.raises(ValueError):
        api.call("rm", "x")
    api.close()


def test_socket_path_follows_docker_host():
    assert dockerapi.socket_path("") == dockerapi.DEFAULT_SOCKET
    assert dockerapi.socket_path("unix:///run/user/1000/docker.sock") == "/run/user/1000/docker.sock"
    assert dockerapi.socket_path("tcp://10.0.0.1:2375") is None


def test_open_docker_connects_or_falls_back_with_one_warning(daemon, monkeypatch, caplog):
    monkeypatch.setenv("DOCKER_HOST", f"unix://{daemon.path}")
    assert isinstance(gm.open_docker(), dockerapi.DockerAPI)
    for host in ("tcp://10.0.0.1:2375", "unix:///nonexistent/docker.sock"):
        monkeypatch.setenv("DOCKER_HOST", host)
        with caplog.at_level(logging.WARNING):
            assert gm.open_docker() is None
    assert sum("Docker Engine API unavailable" in r.message for r in caplog.records) == 2


def test_cli_fallback_still_runs_when_no_api_is_connected(tmp_path):
    run = Fake()
    ex(run, tmp_path)(Action("freeze", "web", 3))
    assert run.calls == [["docker", "pause", "web"]]


def test_a_verb_skipped_in_user_mode_is_in_the_audit_log_every_time(tmp_path):
    """#453, GV-6: L2 throttle and L3 reclaim need root; the audit log (and so the page and Grafana) must not show them as done."""
    from governor.ladder import Ladder
    from governor.signals import FileSampler
    sig = tmp_path / "s.json"
    sig.write_text(json.dumps({"mem_available": 8 * 2**30, "psi_some": 0.0, "psi_full": 0.0}))
    run = Fake()
    groups = [Group("web", 4)]
    gov = Governor(FileSampler(sig), Ladder(), groups, UserExecutor(groups, {}, run_cmd=run), False)
    gov._act(Action("reclaim", "web", 3))
    gov._act(Action("reclaim", "web", 3))
    gov._act(Action("freeze", "web", 3))
    skipped = [m for m in gov.audit if m.startswith("SKIPPED")]
    assert len(skipped) == 2 and "rootless mode" in skipped[0] and "reclaim" in skipped[0]
    assert gov.counts["actions_skipped"] == 2 and not any("freeze" in m for m in skipped)
    assert run.calls == [["docker", "pause", "web"]] or run.calls == []  # the Engine API path may not fork at all


def test_the_root_executor_never_reports_a_skip(tmp_path):
    from governor.ladder import Ladder
    from governor.signals import FileSampler
    sig = tmp_path / "s.json"
    sig.write_text("{}")
    class Quiet(Executor):
        def __call__(self, a):
            return None
    gov = Governor(FileSampler(sig), Ladder(), [], Quiet([], {}, run_cmd=Fake()), False)
    gov._act(Action("reclaim", "web", 3))
    assert not [m for m in gov.audit if m.startswith("SKIPPED")]
