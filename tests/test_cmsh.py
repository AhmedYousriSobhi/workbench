import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("cmsh", ROOT / "images/login/cmsh.py")
cmsh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmsh)

DEVICES = [{"hostname": "cn001", "childType": "PhysicalNode", "category": "compute", "ip": "10.141.0.1", "status": "UP"},
           {"hostname": "gpu001", "childType": "PhysicalNode", "category": "gpu", "ip": "10.141.0.2", "status": "DOWN"}]


@pytest.fixture(autouse=True)
def bcm(monkeypatch):
    monkeypatch.setattr(cmsh.socket, "gethostname", lambda: "login")
    monkeypatch.setattr(cmsh, "call", lambda service, name, args=None: DEVICES)


def test_device_list_is_a_table_with_the_head_node_first():
    code, out, err = cmsh.run("device; list")
    lines = out.splitlines()
    assert code == 0 and not err
    assert lines[0].split()[:2] == ["Type", "Hostname"] and set(lines[1].replace(" ", "")) == {"-"}
    assert re.match(r"HeadNode\s+login\s+FA:16:3E:00:00:FE\s+10\.141\.255\.254\s+internalnet\s+\[   UP   \]$", lines[2])
    assert re.match(r"PhysicalNode\s+gpu001\s+FA:16:3E:00:00:02\s+gpu\s+10\.141\.0\.2\s+internalnet\s+\[  DOWN  \]$", lines[4])


def test_device_status_follows_the_node_state():
    out = cmsh.run("device; status")[1].splitlines()
    assert out[0].startswith("login ") and re.search(r"\.{2,} \[   UP   \]$", out[0])
    assert re.match(r"gpu001 \.+ \[  DOWN  \]$", out[2])


def test_category_jobqueue_and_healthchecks(monkeypatch):
    out = cmsh.run("category; list")[1]
    assert "compute" in out and "gpu" in out
    monkeypatch.setattr(cmsh.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "cpu 4\ngpu 2\n"})())
    assert cmsh.run("jobqueue; list")[1].splitlines()[2].split() == ["slurm", "cpu", "4"]
    assert "DeviceIsUp" in cmsh.run("monitoring; healthchecks; list")[1]


@pytest.mark.parametrize("script", ["device; set cn001 category x", "device; use cn001; commit", "device; power off cn001",
                                    "category; add foo", "device; foreach -n cn001 (reboot)"])
def test_anything_that_changes_state_is_refused(script):
    code, out, err = cmsh.run(script)
    assert code == 1 and not out and "read-only" in err


def test_unknown_commands_and_empty_scripts():
    assert cmsh.run("nosuchmode; list")[0] == 1 and "unknown command" in cmsh.run("nosuchmode; list")[2]
    assert cmsh.run("") == (0, "", "")


def test_unreachable_bcm_mock_is_an_error_not_an_empty_table(monkeypatch):
    def boom(*a, **k):
        raise cmsh.Unreachable("connection refused")

    monkeypatch.setattr(cmsh, "call", boom)
    code, out, err = cmsh.run("device; list")
    assert code == 1 and not out and "Unable to connect to CMDaemon" in err


def test_main_handles_flags_and_version(capsys):
    assert cmsh.main(["--hide-events", "-c", "device; status"]) == 0
    assert "login " in capsys.readouterr().out
    assert cmsh.main(["--version"]) == 0 and "cmsh" in capsys.readouterr().out


def test_call_reports_http_and_service_errors(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    monkeypatch.undo()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b'{"result": [1]}'))
    assert cmsh.call("cmdevice", "getDevices") == [1]
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b'{"error": "Unknown call"}'))
    with pytest.raises(cmsh.Unreachable, match="Unknown call"):
        cmsh.call("x", "y")

    def refuse(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(cmsh.Unreachable):
        cmsh.call("x", "y")


def session(monkeypatch, text):
    import io
    monkeypatch.setattr(cmsh.socket, "gethostname", lambda: "login")
    monkeypatch.setattr(cmsh, "call", lambda service, name, args=None: DEVICES)
    out, err = io.StringIO(), io.StringIO()
    code = cmsh.console(io.StringIO(text), out, err)
    return code, out.getvalue(), err.getvalue()


def test_interactive_console_enters_modes_lists_and_leaves(monkeypatch):
    code, out, err = session(monkeypatch, "device\nlist\n..\nmonitoring\nhealthchecks\nlist\nexit\n")
    assert code == 0 and not err
    assert "[login]% " in out and "[login->device]% " in out and "[login->monitoring->healthchecks]% " in out
    assert "Hostname (key)" in out and "DeviceIsUp" in out
    assert session(monkeypatch, "device; status\n")[1].count("[   UP   ]") >= 1  # one line, several statements


def test_interactive_console_is_read_only_and_reports_unknown_commands(monkeypatch):
    _, out, err = session(monkeypatch, "device\nset cn001 category x\nfrobnicate\nhelp\n")
    assert "read-only" in err and "unknown command: frobnicate" in err and "Commands here (device)" in out
    assert session(monkeypatch, "")[0] == 0  # end of input leaves cleanly


def test_main_without_c_opens_the_console(monkeypatch):
    import io
    monkeypatch.setattr(cmsh.socket, "gethostname", lambda: "login")
    monkeypatch.setattr(cmsh.sys, "stdin", io.StringIO("exit\n"))
    assert cmsh.main([]) == 0


def test_help_lists_the_commands_of_the_current_mode(monkeypatch):
    _, out, _ = session(monkeypatch, "help\ndevice\nhelp\n")
    assert "device" in out and "category" in out and "exit" in out          # top level
    assert "list" in out and "show" in out and "status" in out and ".." in out  # inside device


def test_show_prints_one_device_or_category(monkeypatch):
    _, out, err = session(monkeypatch, "device\nshow cn001\nshow nope\n..\ncategory\nshow compute\n")
    assert "Hostname" in out and "cn001" in out and "no such device: nope" in err.replace("Unable to connect to CMDaemon: ", "")
    assert "Software image" in out and "default-image-compute" in out


def test_tab_completion_offers_modes_commands_and_node_names(monkeypatch):
    monkeypatch.setattr(cmsh.socket, "gethostname", lambda: "login")
    monkeypatch.setattr(cmsh, "call", lambda service, name, args=None: DEVICES)
    assert cmsh.completions([], "de", "de") == ["device"] and "monitoring" in cmsh.completions([], "", "")
    assert cmsh.completions(["device"], "", "") == sorted(["list", "status", "show", "..", "help", "exit", "quit"])
    assert cmsh.completions(["device"], "show cn", "cn") == [d["hostname"] for d in DEVICES if d["hostname"].startswith("cn")]
    assert cmsh.completions(["device"], "list; sh", "sh") == ["show"]  # a new statement after ;
    assert cmsh.completions(["monitoring"], "h", "h") == ["healthchecks", "help"]
    assert cmsh.completions(["jobqueue"], "list ", "") == []
