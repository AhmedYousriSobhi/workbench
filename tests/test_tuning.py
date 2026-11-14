import json

from typer.testing import CliRunner

from benchctl import checks, cli, tuning

MEM = "MemTotal:       16777216 kB\nSwapTotal: 0 kB\n"
runner = CliRunner()


def test_one_block_per_setting_with_command_effect_and_undo():
    items = tuning.plan(MEM, zram=False, swappiness="60", thp="[always] madvise never")
    assert [t.setting for t in items] == ["zram", "swappiness", "thp"]
    for t in items:
        assert t.command.startswith(("sudo", "echo")) and t.effect and t.undo and not t.applied
    z, s, h = items
    assert "--size 4096M" in z.command and "--algorithm zstd" in z.command
    assert s.command == "sudo sysctl vm.swappiness=100" and s.undo == "sudo sysctl vm.swappiness=60"
    assert h.current == "always" and h.undo.startswith("echo always |")


def test_each_block_has_a_persistent_form_and_its_undo():
    z, s, h = tuning.plan(MEM, zram=False, swappiness="60", thp="[always] madvise never")
    assert "/etc/sysctl.d/99-workbench.conf" in s.persist and "sudo sysctl --system" in s.persist
    assert s.persist_undo == "sudo rm /etc/sysctl.d/99-workbench.conf && sudo sysctl vm.swappiness=60"
    assert "zram-size = ram / 4" in z.persist and "systemd-zram-setup@zram0.service" in z.persist and "swapoff" in z.persist_undo
    assert "transparent_hugepage=madvise" in h.persist and "update-grub" in h.persist_undo
    out = tuning.render([z, s, h])
    assert out.count("persist:") == 3 and out.count("persist undo:") == 3


def test_settings_already_right_are_applied_and_not_rendered():
    items = tuning.plan(MEM, zram=True, swappiness="100", thp="always [madvise] never")
    assert all(t.applied for t in items) and tuning.render(items) == ""


def test_render_says_print_only_and_lists_each_undo():
    out = tuning.render(tuning.plan(MEM, zram=False, swappiness="60", thp="[always] madvise never"))
    assert "never runs sudo" in out and out.count("  undo:") == 3 and out.count("effect:") == 3


def test_plan_never_executes_anything(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("executed")))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("executed")))
    tuning.plan(MEM)  # real /proc reads only


def test_unreadable_host_files_do_not_crash():
    items = tuning.plan("", zram=False, swappiness=None, thp="")
    assert items[1].current in ("unknown", items[1].current) and len(items) == 3


def _patch(monkeypatch, tune):
    monkeypatch.setattr(checks, "run_all", lambda: [checks.Check("swap", checks.OK, "0% used")])
    monkeypatch.setattr(tuning, "plan", lambda: tune)


def test_doctor_text_and_json_and_agent_mode(monkeypatch):
    tune = tuning.plan(MEM, zram=False, swappiness="60", thp="[always] madvise never")
    _patch(monkeypatch, tune)
    text = runner.invoke(cli.app, ["doctor"]).stdout
    assert "sudo sysctl vm.swappiness=100" in text and "undo:" in text
    data = json.loads(runner.invoke(cli.app, ["doctor", "--json"]).stdout)
    assert [t["setting"] for t in data["tuning"]] == ["zram", "swappiness", "thp"]
    monkeypatch.setenv("BENCHCTL_AGENT", "1")
    agent = runner.invoke(cli.app, ["doctor"]).stdout
    assert agent.count("\n") == 2 and "tuning: zram, swappiness, thp" in agent and "sudo" not in agent


def test_doctor_agent_mode_is_silent_when_tuned(monkeypatch):
    _patch(monkeypatch, tuning.plan(MEM, zram=True, swappiness="100", thp="madvise"))
    monkeypatch.setenv("BENCHCTL_AGENT", "1")
    assert runner.invoke(cli.app, ["doctor"]).stdout.strip() == "ok: every check passed"
