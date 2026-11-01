"""governor.__main__.main() wired end to end in-process, with the host-touching parts replaced."""
import json
import logging
import os
import socket

import pytest

from governor import __main__ as gm
from governor import control
from governor.planner import Group


@pytest.fixture
def host(monkeypatch, tmp_path):
    """No Docker, no real mlockall, SIGTERM handler left alone; a signals file the ladder reads."""
    calls = {"locked": 0}
    monkeypatch.setattr(gm, "discover_docker", lambda: ([Group("web", 4, "docker")], {}))
    monkeypatch.setattr(gm, "lock_memory", lambda: calls.__setitem__("locked", calls["locked"] + 1))
    monkeypatch.setattr(gm, "raise_memlock_limit", lambda: None)
    monkeypatch.setattr(gm, "locked_kb", lambda: 123)
    monkeypatch.setattr(gm.signal, "signal", lambda *a: None)
    sig = tmp_path / "sig.json"
    sig.write_text(json.dumps({"mem_available": 8 * 2**30, "psi_some": 0.0, "psi_full": 0.0}))
    return calls, sig


def test_once_samples_logs_and_exits(host, caplog):
    calls, sig = host
    with caplog.at_level(logging.INFO, logger="governor"):
        gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig)])
    assert calls["locked"] == 1
    assert any("L0 avail=8.00GB" in r.message for r in caplog.records)
    assert any("memory locked: 123 kB" in r.message for r in caplog.records)


def test_thresholds_override_changes_the_level(host, caplog):
    _, sig = host
    with caplog.at_level(logging.INFO, logger="governor"):
        gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig),
                 "--thresholds", json.dumps({"l1_mem": 16 * 2**30})])  # 8 GB free is now "low"
    levels = [r.message for r in caplog.records if " avail=" in r.message]
    assert levels and not levels[0].startswith("L0")


def test_vm_flag_adds_a_vm_group(host, caplog):
    _, sig = host
    with caplog.at_level(logging.INFO, logger="governor"):
        gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig), "--vm", "fidelity=6"])
    inventory = next(r.message for r in caplog.records if r.message.startswith("inventory:"))
    assert "('fidelity', 6)" in inventory and "('web', 4)" in inventory


def test_control_socket_is_served_during_the_run_and_removed_after(host, tmp_path, monkeypatch):
    _, sig = host
    path = tmp_path / "run" / "gov.sock"
    seen = {}
    real_serve = control.serve

    def spy(p, handle, groups, **kw):
        srv = real_serve(p, handle, groups, **kw)
        seen["exists"] = os.path.exists(p)
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(p)
            s.sendall(b'{"cmd": "status"}\n')
            seen["reply"] = json.loads(s.makefile().readline())
        return srv

    monkeypatch.setattr(gm.control, "serve", spy)
    gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig), "--socket", str(path)])
    assert seen["exists"] and seen["reply"]["ok"] and seen["reply"]["groups"] == ["web"]
    assert not path.exists()  # cleaned up on exit


def test_require_lock_exits_2_when_mlockall_fails(host, monkeypatch):
    _, sig = host

    def deny():
        raise OSError(12, "Cannot allocate memory")

    monkeypatch.setattr(gm, "lock_memory", deny)
    with pytest.raises(SystemExit) as e:
        gm.main(["--dry-run", "--once", "--signals-file", str(sig), "--require-lock", "--no-watchdog"])
    assert e.value.code == 2


def test_lock_failure_without_require_lock_only_logs(host, monkeypatch, caplog):
    _, sig = host
    monkeypatch.setattr(gm, "lock_memory", lambda: (_ for _ in ()).throw(OSError(12, "nope")))
    with caplog.at_level(logging.ERROR, logger="governor"):
        gm.main(["--dry-run", "--once", "--signals-file", str(sig), "--no-watchdog"])
    assert any("mlockall failed" in r.message for r in caplog.records)


def test_metrics_endpoint_failure_does_not_stop_the_governor(host, monkeypatch, caplog):
    _, sig = host

    def boom(addr, gov):
        raise OSError("address in use")

    monkeypatch.setattr(gm.metrics, "serve", boom)
    with caplog.at_level(logging.ERROR, logger="governor"):
        gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig), "--metrics-addr", "127.0.0.1:1"])
    assert any("metrics endpoint unavailable" in r.message for r in caplog.records)


def test_metrics_server_is_started_and_shut_down(host, monkeypatch):
    _, sig = host
    events = []

    class Srv:
        def shutdown(self):
            events.append("shutdown")

    monkeypatch.setattr(gm.metrics, "serve", lambda addr, gov: events.append(addr) or Srv())
    gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig), "--metrics-addr", "127.0.0.1:9"])
    assert events == ["127.0.0.1:9", "shutdown"]


def test_grafana_url_without_token_warns_and_disables_annotations(host, monkeypatch, caplog):
    _, sig = host
    monkeypatch.delenv("WORKBENCH_GRAFANA_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING, logger="governor"):
        gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig), "--grafana-url", "http://x"])
    assert any("annotations disabled" in r.message for r in caplog.records)


def test_effector_runs_benchctl_in_the_workdir(host, monkeypatch, tmp_path):
    _, sig = host
    ran = {}

    class Done:
        returncode = 0

    monkeypatch.setattr(gm.subprocess, "run", lambda cmd, **kw: ran.update(cmd=cmd, cwd=kw["cwd"]) or Done())
    captured = {}
    real_gov = gm.Governor

    def spy(*a, **k):
        g = real_gov(*a, **k)
        captured["effector"] = a[-1]
        return g

    monkeypatch.setattr(gm, "Governor", spy)
    gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig), "--workdir", str(tmp_path)])
    assert captured["effector"](["scale", "2"]) == 0
    assert ran["cwd"] == str(tmp_path) and ran["cmd"][-3:] == ["benchctl.cli", "scale", "2"]


def test_without_workdir_there_is_no_effector(host, monkeypatch):
    _, sig = host
    captured = {}
    real_gov = gm.Governor
    monkeypatch.setattr(gm, "Governor", lambda *a, **k: captured.update(eff=a[-1]) or real_gov(*a, **k))
    gm.main(["--dry-run", "--once", "--no-watchdog", "--signals-file", str(sig)])
    assert captured["eff"] is None  # scale/set-profile are refused unless --workdir is given
