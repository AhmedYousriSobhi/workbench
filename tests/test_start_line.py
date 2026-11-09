"""`benchctl start` names what it starts and what it replaces (#215)."""
from benchctl import workbench


def saved(**kw):
    return workbench.Spec(**{"cpu_nodes": 2, "gpu_nodes": 3, "parts": ["jira", "bmc"], **kw})


def test_nothing_saved_just_says_what_starts():
    assert workbench.start_line(None, None, False) == "Starting the min size preset."
    assert workbench.start_line(None, "std", False) == "Starting the std size preset."
    assert workbench.start_line(None, None, True) == "Starting the default workbench plus the parts you named."


def test_a_saved_workbench_is_named_as_replaced():
    line = workbench.start_line(saved(), "min", False)
    assert "min size preset" in line and "replaces the workbench last built" in line
    assert "2 compute + 3 GPU nodes" in line and "jira, bmc" in line and "apply -f FILE" in line


def test_parts_start_from_the_default_not_the_saved_one():
    assert "default workbench plus the parts" in workbench.start_line(saved(), None, True)


def test_a_saved_workbench_with_no_parts_reads_plainly():
    assert "parts none" in workbench.start_line(saved(parts=[]), "min", False)


def test_nothing_chosen_keeps_the_saved_workbench(tmp_path):
    """Issue 387: `start` with no flags and a saved workbench reuses it; an explicit preset or parts still replace it."""
    assert workbench.reuses_saved(saved(), None, False)
    assert not workbench.reuses_saved(saved(), "min", False) and not workbench.reuses_saved(saved(), None, True) and not workbench.reuses_saved(None, None, False)
    line = workbench.start_line(saved(), None, False)
    assert "last built" in line and "kept" in line and "replaces" not in line and "--profile min" in line


def test_start_passes_saved_to_the_launcher_only_when_it_reuses_it(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from benchctl import cli
    calls = []
    monkeypatch.setattr(cli, "_launcher", lambda *a: calls.append(a))
    monkeypatch.setattr(cli, "_offer_completion", lambda: None)
    here = {"spec": saved()}
    monkeypatch.setattr(workbench, "current", lambda sim, path=None: here["spec"])
    run = CliRunner().invoke
    assert run(cli.app, ["start"]).exit_code == 0 and calls[-1] == ("start", "--saved")
    assert run(cli.app, ["start", "--profile", "min"]).exit_code == 0 and calls[-1] == ("start", "--profile", "min")
    assert run(cli.app, ["start", "--with", "jira"]).exit_code == 0 and calls[-1][:2] == ("start", "--with")
    here["spec"] = None
    assert run(cli.app, ["start"]).exit_code == 0 and calls[-1] == ("start", "--profile", "min")
