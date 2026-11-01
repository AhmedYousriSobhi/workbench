"""Parts by category (#184): --ticketing jira, --hardware bmc,bcm, --addons, and the Tab completion that guides them."""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchctl import cli, completion, workbench

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def in_repo(monkeypatch):
    monkeypatch.chdir(ROOT)


def test_every_part_is_in_exactly_one_category():
    listed = [p for _, items in workbench.CATEGORIES.values() for p in items.values() if p]
    assert sorted(listed) == sorted(workbench.PARTS)
    assert set(workbench.PART_CATEGORY) == set(workbench.PARTS)
    assert all("category" in row for row in workbench.catalog())


def test_expand_names_items_with_commas_repeats_and_all():
    assert workbench.expand({"hardware": ["bmc,bcm"], "access": ["teleport"]}) == ["bcm", "bmc", "teleport"]
    assert workbench.expand({"hardware": ["bmc", "bcm"]}) == ["bcm", "bmc"]
    assert workbench.expand({"simulation": ["all"]}) == ["load", "scenarios"]
    assert workbench.expand({"accounting": ["slurmdbd"]}) == ["accounting"]
    assert workbench.expand({"workload": ["slurm"], "monitoring": ["grafana,prometheus"]}) == []  # always on: nothing to add
    assert workbench.expand({}, addons=True) == sorted(workbench.PARTS)


def test_expand_says_why_an_item_is_refused():
    with pytest.raises(workbench.SpecError, match=r"unknown ticketing item 'jirra'. Did you mean 'jira'\? Valid: jira, all"):
        workbench.expand({"ticketing": ["jirra"]})
    with pytest.raises(workbench.SpecError, match="Kubernetes is not built yet"):
        workbench.expand({"workload": ["kubernetes"]})
    with pytest.raises(workbench.SpecError, match="Valid: bmc, bcm, all"):
        workbench.expand({"hardware": ["bmc,nope"]})


def test_completion_offers_flags_items_and_continues_after_a_comma():
    assert completion.category("hardware")("") == ["bmc", "bcm", "all"]
    assert completion.category("hardware")("b") == ["bmc", "bcm"]
    assert completion.category("hardware")("bmc,") == ["bmc,bcm", "bmc,all"]
    assert completion.category("hardware")("bmc,b") == ["bmc,bcm"]
    assert "kubernetes" not in completion.category("workload")("")  # not built: never suggested
    assert completion.category("access")("t") == ["teleport"]
    out = runner.invoke(cli.app, ["start", "--help"], terminal_width=200).output
    for flag in ("--workload", "--accounting", "--monitoring", "--ticketing", "--hardware", "--access", "--simulation", "--addons"):
        assert flag in out


def test_plan_accepts_category_flags_and_addons():
    r = runner.invoke(cli.app, ["plan", "--ticketing", "jira", "--hardware", "bmc,bcm", "--access", "teleport"])
    assert "teleport" in r.output and "jira" in r.output and "bcm" in r.output
    r = runner.invoke(cli.app, ["plan", "--addons", "--without", "teleport"])
    assert "load" in r.output and "teleport" not in r.output.split("(always")[0]
    r = runner.invoke(cli.app, ["plan", "--hardware", "bmcc"])
    assert r.exit_code == 1 and "Did you mean 'bmc'?" in r.output


def test_start_forwards_the_selection_as_parts(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_launcher", lambda *a: calls.append(a))
    monkeypatch.setattr(cli, "_offer_completion", lambda: None)
    runner.invoke(cli.app, ["start", "--access", "teleport", "--simulation", "load"])
    args = calls[-1]
    assert args[0] == "start" and "--profile" not in args
    assert [args[i + 1] for i, a in enumerate(args) if a == "--with"] == ["load", "teleport"]
    runner.invoke(cli.app, ["start", "--workload", "slurm"])  # adds nothing, still a selection (not the min preset)
    assert "--profile" not in calls[-1]
    runner.invoke(cli.app, ["start"])
    assert calls[-1] == ("start", "--profile", "min")
    r = runner.invoke(cli.app, ["start", "--profile", "std", "--addons"])
    assert r.exit_code == 1 and "choose one" in r.output


def test_up_without_flags_is_still_a_preset(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.stack, "up", lambda sim, profile, **kw: seen.setdefault("profile", profile) or True)
    monkeypatch.setattr(cli.workbench, "forget", lambda: None)
    runner.invoke(cli.app, ["up"])
    assert seen["profile"] == "std"
