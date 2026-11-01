"""Choose the workbench by counts and parts, estimate, then build only if it fits (issue 92)."""
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, compose, stack, workbench

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
runner = CliRunner()


def spec(**kw):
    return workbench.from_dict(kw, workbench.defaults(SIM))


def test_defaults_come_from_sim_yaml_and_flags_override_only_what_they_name():
    d = workbench.defaults(SIM)
    assert (d.cpu_nodes, d.gpu_nodes, d.gpus_per_node) == (4, 2, 8) and sorted(d.parts) == ["accounting", "bcm", "bmc", "jira", "scenarios"]
    assert spec(cpu_nodes=2, gpu_nodes=4).gpu_nodes == 4 and spec(cpu_nodes=2).gpus_per_node == 8


def test_problems_name_every_bad_field():
    s = spec(cpu_nodes=99, gpus_per_node=9, parts=["jira", "nope"], k8s_nodes=2)
    text = " | ".join(s.problems())
    assert "cpu_nodes must be 0 to 64" in text and "gpus_per_node must be 1 to 8" in text
    assert "unknown part 'nope'" in text and "Kubernetes nodes are not built" in text
    assert "at least one node" in " ".join(spec(cpu_nodes=0, gpu_nodes=0).problems())
    assert spec().problems() == []


def test_unknown_file_field_is_refused(tmp_path):
    f = tmp_path / "w.yaml"
    f.write_text("cpu_nodes: 2\ngpus: 4\n")
    with pytest.raises(workbench.SpecError, match="gpus"):
        workbench.load_file(f, workbench.defaults(SIM))
    f.write_text("- a\n")
    with pytest.raises(workbench.SpecError, match="mapping"):
        workbench.load_file(f, workbench.defaults(SIM))
    with pytest.raises(workbench.SpecError, match="cannot read"):
        workbench.load_file(tmp_path / "none.yaml", workbench.defaults(SIM))


def test_effective_sim_and_profile_follow_the_selection():
    sim, prof = workbench.effective(SIM, spec(cpu_nodes=2, gpu_nodes=4, parts=["jira"]))
    assert (sim["compute"]["count"], sim["gpu"]["nodes"]) == (2, 4)
    assert prof["groups"] == ["core", "access", "mocks"] and sorted(prof["drop"]) == ["bcm-mock", "bmc-farm"]
    services = compose.generate(sim, prof)["services"]
    assert "jira-mock" in services and "bmc-farm" not in services and "bcm-mock" not in services
    assert [n for n in services if n.startswith(("cn", "gpu"))] == ["cn001", "cn002", "gpu001", "gpu002", "gpu003", "gpu004"]


def test_a_partition_with_no_nodes_is_dropped():
    sim, _ = workbench.effective(SIM, spec(cpu_nodes=0, gpu_nodes=2))
    assert [p["name"] for p in sim["partitions"]] == ["gpu"]


def test_estimate_is_the_sum_of_the_generated_limits_plus_observability():
    sim, prof = workbench.effective(SIM, spec(cpu_nodes=2, gpu_nodes=4))
    est = workbench.estimate(sim, prof)
    data = compose.generate(sim, prof)
    assert est["total_mb"] == compose.budget_mb(data) + workbench.OBSERVABILITY_MB
    assert est["node_count"] == 6 and est["nodes_mb"] == 6 * compose.SLURMD_MB
    more = workbench.estimate(*workbench.effective(SIM, spec(cpu_nodes=10, gpu_nodes=4)))
    assert more["total_mb"] - est["total_mb"] == 8 * (compose.SLURMD_MB + compose.BMC_FORWARDER_MB)  # a node and its BMC


def test_observability_constant_matches_the_static_compose_file():
    svcs = yaml.safe_load((ROOT / "compose/observability.yml").read_text())["services"]
    assert sum(int(s["mem_limit"].rstrip("m")) for s in svcs.values()) == workbench.OBSERVABILITY_MB


def test_verdict_fits_or_says_what_to_remove():
    s = spec(cpu_nodes=30, gpu_nodes=16, parts=["accounting", "load", "jira"])
    est = workbench.estimate(*workbench.effective(SIM, s))
    ok, msg = workbench.verdict(est, est["total_mb"] + 2048)
    assert ok and "fits" in msg
    ok, msg = workbench.verdict(est, est["total_mb"] - 200, spec=s)
    assert not ok and "short by" in msg and "fewer node" in msg and "drop load" in msg and "drop accounting" in msg


def test_render_lists_nodes_parts_and_total():
    s = spec(cpu_nodes=2, gpu_nodes=4)
    text = workbench.render(s, workbench.estimate(*workbench.effective(SIM, s)))
    assert "2 cpu, 4 gpu x 8 GPUs" in text and "total" in text and "jira" in text


def test_save_current_forget_round_trip(tmp_path):
    path = tmp_path / "w.yaml"
    assert workbench.current(SIM, path) is None
    workbench.save(spec(cpu_nodes=3, parts=["bmc"]), path)
    got = workbench.current(SIM, path)
    assert got.cpu_nodes == 3 and got.parts == ["bmc"]
    workbench.forget(path)
    assert workbench.current(SIM, path) is None


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    """A scratch repo root with the real sim.yaml and profiles, so nothing touches the real generated tree."""
    (tmp_path / "sim.yaml").write_text((ROOT / "sim.yaml").read_text())
    (tmp_path / "profiles").symlink_to(ROOT / "profiles")
    (tmp_path / "scenarios").symlink_to(ROOT / "scenarios")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workbench, "SAVED", tmp_path / "compose/generated/workbench.yaml")
    monkeypatch.setattr("benchctl.checks.mem_available", lambda *a: 8 * 1024**3)
    return tmp_path


def test_plan_prints_the_estimate_and_exits_0_when_it_fits(cwd):
    r = runner.invoke(cli.app, ["plan", "--cpu-nodes", "2", "--gpu-nodes", "4", "--with", "jira"])
    assert r.exit_code == 0 and "2 cpu, 4 gpu" in r.output and "fits:" in r.output


def test_plan_exits_1_and_names_the_fix_when_it_does_not_fit(cwd, monkeypatch):
    monkeypatch.setattr("benchctl.checks.mem_available", lambda *a: 1500 * 1024**2)
    r = runner.invoke(cli.app, ["plan", "--cpu-nodes", "20", "--with", "load"])
    assert r.exit_code == 1 and "does not fit" in r.output and "Try:" in r.output


def test_plan_refuses_bad_input_with_every_problem(cwd):
    r = runner.invoke(cli.app, ["plan", "--cpu-nodes", "99", "--k8s-nodes", "2", "--with", "zzz"])
    assert r.exit_code == 1 and r.output.count("invalid:") == 3


def test_plan_save_writes_a_file_that_apply_reads_back(cwd):
    r = runner.invoke(cli.app, ["plan", "--cpu-nodes", "2", "--gpu-nodes", "4", "--without", "bcm", "--save", "bench.yaml"])
    assert r.exit_code == 0 and "apply -f bench.yaml" in r.output
    saved = yaml.safe_load(Path("bench.yaml").read_text())
    assert saved["cpu_nodes"] == 2 and saved["gpu_nodes"] == 4 and "bcm" not in saved["parts"]


def test_up_by_selection_builds_with_the_workbench_and_remembers_it(cwd, monkeypatch):
    seen = {}

    def fake_up(sim, profile, runner=None, say=print, available=None, wait=120, prof=None):
        seen.update(sim=sim, profile=profile, prof=prof)
        return True
    monkeypatch.setattr(stack, "up", fake_up)
    r = runner.invoke(cli.app, ["up", "--cpu-nodes", "2", "--gpu-nodes", "4", "--with", "jira"])
    assert r.exit_code == 0, r.output
    assert seen["sim"]["gpu"]["nodes"] == 4 and "jira-mock" in compose.generate(seen["sim"], seen["prof"])["services"]
    assert workbench.current(SIM).gpu_nodes == 4
    assert json.loads(runner.invoke(cli.app, ["get", "workbench", "-o", "json"]).output)["cpu_nodes"] == 2
    assert "gpu004" in runner.invoke(cli.app, ["get", "nodes"]).output  # every command now sees the built workbench


def test_a_failed_start_does_not_remember_the_workbench(cwd, monkeypatch):
    monkeypatch.setattr(stack, "up", lambda *a, **k: False)
    assert runner.invoke(cli.app, ["up", "--cpu-nodes", "2"]).exit_code == 1
    assert workbench.current(SIM) is None


def test_plain_profile_up_forgets_the_workbench(cwd, monkeypatch):
    workbench.save(spec(cpu_nodes=2))
    monkeypatch.setattr(stack, "up", lambda *a, **k: True)
    assert runner.invoke(cli.app, ["up", "--profile", "min"]).exit_code == 0
    assert workbench.current(SIM) is None
    assert "none:" in runner.invoke(cli.app, ["get", "workbench"]).output


def test_profile_and_selection_cannot_be_mixed(cwd):
    r = runner.invoke(cli.app, ["up", "--profile", "min", "--cpu-nodes", "2"])
    assert r.exit_code == 1 and "choose one" in r.output


def test_apply_needs_a_file_and_builds_it(cwd, monkeypatch):
    monkeypatch.setattr(stack, "up", lambda *a, **k: True)
    Path("w.yaml").write_text("cpu_nodes: 1\ngpu_nodes: 1\nparts: [bmc]\n")
    assert runner.invoke(cli.app, ["apply", "-f", "w.yaml"]).exit_code == 0
    assert workbench.current(SIM).parts == ["bmc"]
    assert runner.invoke(cli.app, ["apply"]).exit_code == 2
    Path("bad.yaml").write_text("cpu_nodes: [1]\n")
    assert runner.invoke(cli.app, ["apply", "-f", "bad.yaml"]).exit_code != 0


def test_stack_up_admits_a_workbench_by_its_own_estimate(cwd):
    said = []
    sim, prof = workbench.effective(SIM, spec(cpu_nodes=2, gpu_nodes=1))

    def fake(cmd):
        return subprocess.CompletedProcess(cmd, 0, "", "")
    ok = stack.up(sim, "workbench", runner=fake, say=said.append, available=200 * 1024**2, prof=prof)
    assert not ok and any(m.startswith("REFUSED") and "does not fit" in m for m in said)


def test_compose_writes_the_effective_sim_next_to_the_file(cwd):
    sim, prof = workbench.effective(SIM, spec(cpu_nodes=2, gpu_nodes=4))
    path = compose.write(compose.generate(sim, prof), sim=sim)
    copy = yaml.safe_load((path.parent / "sim.yaml").read_text())
    assert copy["compute"]["count"] == 2 and copy["gpu"]["nodes"] == 4
    assert (path.parent / "slurm" / "slurm.conf").read_text().count("gpu[001-004]") >= 1
    assert "bmc-gpu003" in compose.generate(sim, prof)["services"]  # every node has a BMC forwarder


def test_export_follows_the_built_workbench(cwd, monkeypatch):
    monkeypatch.setattr(stack, "up", lambda *a, **k: True)
    assert runner.invoke(cli.app, ["up", "--cpu-nodes", "1", "--gpu-nodes", "1", "--with", "bmc", "--without", "jira"]).exit_code == 0
    r = runner.invoke(cli.app, ["export", "profile", "--format", "demo", "--bmc", "--configured", "-o", "-"])
    body = json.loads(r.stdout)
    assert body["clusters"][0]["jira"] is None and [t["node"] for t in body["bmc"]] == ["cn001", "gpu001"]


def test_scale_refuses_while_a_workbench_is_built(cwd):
    workbench.save(spec(cpu_nodes=2))
    r = runner.invoke(cli.app, ["scale", "3"])
    assert r.exit_code == 1 and "a workbench is built" in r.output


def test_web_page_reads_the_generated_sim_while_a_workbench_is_in_use(cwd):
    assert workbench.live_sim_path() == Path("sim.yaml")
    workbench.save(spec(cpu_nodes=2))
    Path("compose/generated").mkdir(parents=True, exist_ok=True)
    Path("compose/generated/sim.yaml").write_text("x: 1\n")
    assert workbench.live_sim_path() == Path("compose/generated/sim.yaml")


def test_catalog_lists_every_part_with_its_cost_and_state():
    cat = {p["id"]: p for p in workbench.catalog(["jira"])}
    assert set(cat) == set(workbench.PARTS) == set(workbench.PART_INFO)
    assert cat["jira"]["on"] and not cat["accounting"]["on"] and cat["accounting"]["mb"] == 360
    assert all(p["label"] and p["help"] and p["mb"] > 0 for p in cat.values())
    assert {p["id"] for p in workbench.catalog() if p["on"]} == set(workbench.DEFAULT_PARTS)


def test_parts_in_reads_what_a_build_contains():
    assert workbench.parts_in({"jira-mock", "bmc-farm"}) == ["jira", "bmc"]
    assert "accounting" in workbench.parts_in({"mariadb", "slurmdbd", "accounting-init"}) and "accounting" not in workbench.parts_in({"slurmdbd"})


def test_bmc_nodes_select_which_nodes_get_a_bmc_and_survive_the_saved_file(tmp_path):
    s = spec(bmc_nodes=["gpu001", "cn002", "cn002"])
    assert s.problems() == [] and s.to_dict()["bmc_nodes"] == ["cn002", "gpu001"]
    sim, prof = workbench.effective(SIM, s)
    assert sim["bmc"] == {"per_node": True, "nodes": ["cn002", "gpu001"]}
    names = sorted(n for n in compose.generate(sim, prof)["services"] if n.startswith("bmc-") and n != "bmc-farm")
    assert names == ["bmc-cn002", "bmc-gpu001"]
    path = tmp_path / "wb.yaml"
    workbench.save(s, path)
    assert workbench.load_file(path, workbench.defaults(SIM)).bmc_nodes == ["cn002", "gpu001"]
    assert "nodes" not in workbench.effective(SIM, spec())[0]["bmc"]  # unset: every node
    assert spec(bmc_nodes=["../x"]).problems() == ["bmc_nodes must be node names such as cn001, gpu002"]
    assert "bmc_nodes" in spec().to_dict() and spec().to_dict()["bmc_nodes"] is None


def test_each_bmc_adds_its_forwarder_to_the_estimate():
    all_on = workbench.estimate(*workbench.effective(SIM, spec()))["total_mb"]
    two = workbench.estimate(*workbench.effective(SIM, spec(bmc_nodes=["cn001", "cn002"])))["total_mb"]
    assert all_on - two == 4 * compose.BMC_FORWARDER_MB
