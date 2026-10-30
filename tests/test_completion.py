"""Tab completion (#105): what each completer offers, the typo messages, and a reversible install."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from simctl import cli, completion

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def in_repo(monkeypatch):
    monkeypatch.chdir(ROOT)


def ctx(name):
    return SimpleNamespace(command=SimpleNamespace(name=name))


def test_value_completers_read_the_repo_files():
    assert {"min", "std"} <= set(completion.profiles(""))
    assert completion.profiles("mi") == ["min"]
    assert completion.scenarios("node-") == ["node-down"]
    assert completion.users("a") == ["alice"]
    assert "jira" in completion.parts("j")
    assert completion.outputs("j") == ["json"]
    assert completion.nodes("gpu") == ["gpu001", "gpu002"]
    assert {"cn001", "slurmctld", "login"} <= set(completion.targets(""))


def test_selector_and_sort_completion_follow_the_resource():
    assert completion.columns(ctx("nodes"), "k") == ["kind"]
    assert completion.columns(ctx("services"), "") == ["name", "state"]
    assert completion.selectors(ctx("nodes"), "ki") == ["kind="]
    assert completion.selectors(ctx("nodes"), "kind=g") == ["kind=gpu"]
    assert completion.selectors(ctx("nodes"), "kind=gpu,st") == ["kind=gpu,state="]
    assert completion.columns(ctx("nowhere"), "") == []


def test_completers_stay_silent_when_the_files_are_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert completion.profiles("") == [] and completion.users("") == [] and completion.nodes("") == []
    assert completion.targets("") == []


def test_explain_names_a_close_match_and_lists_the_valid_values():
    msg = completion.explain("stdd", ["min", "std"], "profile")
    assert "Did you mean 'std'?" in msg and "Valid: min, std." in msg
    assert "Did you mean" not in completion.explain("zzzz", ["min", "std"], "profile")
    assert completion.check_value("min", ["min", "std"], "profile") == "min"
    assert completion.check_value("anything", [], "profile") == "anything"  # nothing to check against
    assert completion.check_value(None, ["min"], "profile") is None


@pytest.mark.parametrize("args,hint", [(["up", "--profile", "stdd"], "Did you mean 'std'?"),
                                        (["describe", "node", "gpu09"], "Valid: cn001"),
                                        (["logs", "slurmct"], "slurmctld")])
def test_a_wrong_value_exits_2_with_one_helpful_message(args, hint):
    r = runner.invoke(cli.app, args)
    assert r.exit_code == 2 and hint in r.output.replace("\n", " ").replace("│", "")


def test_unknown_scenario_suggests_the_close_name():
    r = runner.invoke(cli.app, ["scenario", "run", "nodedown", "--dry-run"])
    assert r.exit_code == 1 and "Did you mean 'node-down'?" in r.output


@pytest.mark.parametrize("shell", completion.SHELLS)
def test_scripts_call_back_into_benchctl(shell):
    text = completion.script(shell)
    assert f"_BENCHCTL_COMPLETE=complete_{shell}" in text
    if shell != "fish":
        assert "./scripts/benchctl" in text


def test_completion_verb_prints_the_script_and_rejects_an_unknown_shell(monkeypatch):
    r = runner.invoke(cli.app, ["completion", "bash"])
    assert r.exit_code == 0 and "complete -o default -F" in r.output
    monkeypatch.delenv("SHELL", raising=False)
    r = runner.invoke(cli.app, ["completion", "install"])
    assert r.exit_code == 2 and "which shell?" in r.output


def test_install_then_remove_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(completion, "_home", lambda: tmp_path)
    rc = tmp_path / ".bashrc"
    rc.write_text("export A=1\n")
    done = completion.install("bash", ROOT)
    assert any("one line" in d for d in done) and (tmp_path / ".local/bin/benchctl").is_symlink()
    assert completion.installed("bash")
    completion.install("bash", ROOT)  # twice adds no second line
    assert rc.read_text().count(completion.MARK_OPEN) == 1
    completion.remove("bash", ROOT)
    assert rc.read_text() == "export A=1\n" and not completion.installed("bash")
    assert not (tmp_path / ".local/bin/benchctl").exists()
    assert completion.remove("bash", ROOT) == ["nothing to remove"]


def test_remove_keeps_a_benchctl_link_that_is_not_ours(tmp_path, monkeypatch):
    monkeypatch.setattr(completion, "_home", lambda: tmp_path)
    (tmp_path / ".local/bin").mkdir(parents=True)
    other = tmp_path / "other"
    other.write_text("x")
    (tmp_path / ".local/bin/benchctl").symlink_to(other)
    completion.install("zsh", ROOT)
    completion.remove("zsh", ROOT)
    assert (tmp_path / ".local/bin/benchctl").is_symlink()


def test_fish_needs_no_rc_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(completion, "_home", lambda: tmp_path)
    completion.install("fish", ROOT)
    assert (tmp_path / ".config/fish/completions/benchctl.fish").is_file() and completion.installed("fish")


def test_start_offers_completion_only_in_a_terminal_and_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(completion, "_home", lambda: tmp_path)
    monkeypatch.setenv("SHELL", "/bin/bash")
    asked = []
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **k: asked.append(a) or False)
    cli._offer_completion()  # the runner has no terminal
    assert asked == [] and not completion.asked_flag().exists()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True, raising=False)
    cli._offer_completion()
    cli._offer_completion()
    assert len(asked) == 1 and completion.asked_flag().exists()
