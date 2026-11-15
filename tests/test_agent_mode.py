"""Agent output mode (SPEC AG-6, AG-8; ADR 0065, 0067): plain output, one-line coded errors, a log in a file, `--yes` only
from BENCHCTL_AGENT=1. Hermetic."""
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchctl import agentmode, checks, cli, inventory, stack, tuning

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def in_repo(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("BENCHCTL_AGENT", raising=False)
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"cn001": "running"})


def agent(monkeypatch):
    monkeypatch.setenv("BENCHCTL_AGENT", "1")


def test_the_two_switches_are_separate(monkeypatch):
    monkeypatch.setattr(agentmode.sys.stdout, "isatty", lambda: False, raising=False)
    assert agentmode.plain() and not agentmode.agent()  # a script: plain output, but no agent rules
    agent(monkeypatch)
    assert agentmode.agent() and agentmode.plain()
    monkeypatch.setenv("BENCHCTL_AGENT", "true")
    assert not agentmode.agent()  # only the value 1


def test_plain_output_sets_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    agent(monkeypatch)
    agentmode.apply()
    assert agentmode.os.environ["NO_COLOR"] == "1"


def test_no_escape_codes_in_non_terminal_output():
    for argv in (["get", "nodes"], ["--help"], ["get", "nodes", "--json"], ["dwon"]):
        r = runner.invoke(cli.app, argv)
        assert "\x1b" not in r.output, argv


def test_a_usage_error_is_one_coded_line_in_agent_mode(monkeypatch):
    human = runner.invoke(cli.app, ["dwon"])
    assert "Usage:" in human.output and "WB-" not in human.output  # people see the same box as before
    agent(monkeypatch)
    r = runner.invoke(cli.app, ["dwon"])
    assert r.exit_code == 2
    assert r.output.strip() == "WB-UNKNOWN-VERB: No such command 'dwon'. Did you mean 'down'? | fix: benchctl --help"


def test_an_unknown_node_has_its_own_code(monkeypatch):
    agent(monkeypatch)
    r = runner.invoke(cli.app, ["describe", "node", "zzz"])
    assert r.exit_code == 2 and r.output.startswith("WB-UNKNOWN-NODE: unknown node 'zzz'") and r.output.strip().endswith("| fix: benchctl get nodes")


def test_preflight_refusal_code_in_agent_mode_only(monkeypatch):
    monkeypatch.setattr(checks, "mem_available", lambda: 1 * 1024**3)
    r = runner.invoke(cli.app, ["preflight", "--profile", "full"])
    assert r.exit_code == 1 and "REFUSED:" in r.output and "WB-" not in r.output
    agent(monkeypatch)
    r = runner.invoke(cli.app, ["preflight", "--profile", "full"])
    assert r.exit_code == 1 and r.output.startswith("WB-REFUSED-MEMORY: ") and "| fix: benchctl plan" in r.output


def test_doctor_in_agent_mode_prints_only_what_needs_attention(monkeypatch):
    tuned = tuning.plan("MemTotal: 1 kB", zram=True, swappiness="100", thp="madvise")
    monkeypatch.setattr(tuning, "plan", lambda: tuned)
    monkeypatch.setattr(checks, "run_all", lambda: [checks.Check("cgroup v2", checks.OK, "fine"), checks.Check("memlock", checks.FAIL, "low\nlimit", fix="raise it")])
    agent(monkeypatch)
    r = runner.invoke(cli.app, ["doctor"])
    assert r.exit_code == 1 and r.output.strip() == "WB-DOCTOR-MEMLOCK fail: low limit | fix: raise it"
    monkeypatch.setattr(checks, "run_all", lambda: [checks.Check("cgroup v2", checks.OK, "fine")])
    r = runner.invoke(cli.app, ["doctor"])
    assert r.exit_code == 0 and r.output.strip() == "ok: every check passed"


def test_destructive_verbs_need_yes_only_in_agent_mode(monkeypatch):
    cancelled = []
    monkeypatch.setattr(cli, "_daemon", lambda req, msg: cancelled.append(msg))
    r = runner.invoke(cli.app, ["remove", "jobs"])
    assert r.exit_code == 0 and cancelled == ["jobs cancelled"]  # a script or a person: unchanged
    cancelled.clear()
    agent(monkeypatch)
    r = runner.invoke(cli.app, ["remove", "jobs"])
    assert r.exit_code == 1 and not cancelled and r.output.startswith("WB-NEEDS-YES: ") and "--yes" in r.output
    assert runner.invoke(cli.app, ["remove", "jobs", "--yes"]).exit_code == 0 and cancelled == ["jobs cancelled"]


def test_down_scale_and_remove_state_need_yes_in_agent_mode(monkeypatch):
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"cn001": "running"})
    monkeypatch.setattr(stack, "down", lambda **k: (_ for _ in ()).throw(AssertionError("must not run")))
    agent(monkeypatch)
    r = runner.invoke(cli.app, ["down"])
    assert r.exit_code == 1 and r.output.startswith("WB-NEEDS-YES: ")
    for argv in (["remove", "teleport-state"], ["remove", "jira-real-state"]):
        r = runner.invoke(cli.app, argv)
        assert r.exit_code == 1 and r.output.startswith("WB-NEEDS-YES: "), argv


def test_a_long_log_goes_to_a_file_and_only_its_path_is_printed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(stack, "_run", lambda cmd: subprocess.CompletedProcess(cmd, 0, "line\n" * 500, ""))
    agent(monkeypatch)
    r = runner.invoke(cli.app, ["logs", "cn001", "--tail", "500"])
    assert r.exit_code == 0 and len(r.output.splitlines()) == 1 and "compose/generated/logs/cn001.log (500 lines" in r.output
    assert (tmp_path / "compose/generated/logs/cn001.log").read_text().count("line") == 500
    assert runner.invoke(cli.app, ["logs", "cn001", "-f"]).output.startswith("WB-USAGE: --follow never ends")


def test_tab_completion_does_not_import_click_or_the_error_machinery_it_does_not_need():
    code = "import sys, benchctl.completion; sys.exit(1 if 'click' in sys.modules or 'mcp' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-I", "-c", code], cwd=ROOT, check=False).returncode == 0
