"""benchctl export profile: shape, omissions, the locally-read format identifier, and no secrets (issue 91)."""
import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, export

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
ALL = {"login", "slurmctld", "grafana", "jira-mock", "bmc-farm"}
runner = CliRunner()


def test_profile_describes_every_part_that_is_built():
    c = export.build(SIM, ALL)["clusters"][0]
    assert c["connection"] == {"host": "127.0.0.1", "port": 2222, "username": "alice", "authMethod": "private-key"}
    assert c["scheduler"]["partitions"] == ["cpu", "gpu"] and c["scheduler"]["kind"] == "slurm"
    assert c["grafana"]["dashboardUids"] == ["hpc-overview"] and c["grafana"]["gpuDatasourceUid"] == "prometheus"
    assert c["jira"]["projectKey"] == "OPS" and c["jira"]["authMode"] == "datacenter"
    assert c["storage"]["paths"] == ["~"] and c["k8s"] is None


def test_parts_that_are_not_built_are_left_out():
    c = export.build(SIM, {"login", "slurmctld"})["clusters"][0]
    assert c["grafana"] is None and c["jira"] is None
    assert export.build(SIM, {"login"})["clusters"][0]["scheduler"] is None


def test_no_login_node_means_nothing_to_connect_to():
    with pytest.raises(export.ExportError, match="no login node"):
        export.build(SIM, {"slurmctld"})


def test_user_must_exist_in_sim_yaml():
    assert export.build(SIM, ALL, user="bob")["clusters"][0]["connection"]["username"] == "bob"
    with pytest.raises(export.ExportError, match="unknown user"):
        export.build(SIM, ALL, user="mallory")


def test_bmc_targets_one_per_node_only_when_asked():
    assert "bmc" not in export.build(SIM, ALL)
    targets = export.build(SIM, ALL, include_bmc=True)["bmc"]
    assert [t["node"] for t in targets][:2] == ["cn001", "cn002"] and len(targets) == 6
    assert all(t["cluster"] == 0 and t["port"] == 8443 and t["username"] == "admin" for t in targets)
    assert "bmc" not in export.build(SIM, {"login"}, include_bmc=True)


def test_file_holds_no_secret_key_path_or_home_directory():
    text = export.to_json(export.build(SIM, ALL, include_bmc=True), ("x", 1))
    assert not re.search(r"password|token|secret|privateKeyPath|/home/|/Users/|BEGIN ", text, re.IGNORECASE)


def test_format_identifier_order_and_errors(tmp_path):
    local = tmp_path / "fmt"
    with pytest.raises(export.ExportError, match=r"\.git/export-format"):
        export.format_id(None, {}, local)
    local.write_text("from-file:3\n")
    assert export.format_id(None, {}, local) == ("from-file", 3)
    assert export.format_id(None, {"BENCH_EXPORT_FORMAT": "from-env"}, local) == ("from-env", 1)
    assert export.format_id("flag:2", {"BENCH_EXPORT_FORMAT": "from-env"}, local) == ("flag", 2)
    with pytest.raises(export.ExportError, match="bad format"):
        export.format_id("x:abc", {}, local)


def test_envelope_carries_the_format_and_version():
    body = json.loads(export.to_json({"profile": {"name": "n"}, "clusters": []}, ("fmt", 4)))
    assert body["format"] == "fmt" and body["version"] == 4 and body["clusters"] == []


def test_command_writes_a_file_and_says_where_the_secrets_are(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    out = tmp_path / "p.json"
    r = runner.invoke(cli.app, ["export", "profile", "--profile", "min", "--bmc", "--format", "demo:1", "-o", str(out)])
    assert r.exit_code == 0, r.output
    data = json.loads(out.read_text())
    assert data["format"] == "demo" and len(data["bmc"]) == 6
    assert "ssh-key install" in r.output and "grafana_viewer_token" in r.output and "bmc_credentials" in r.output
    to_stdout = runner.invoke(cli.app, ["export", "profile", "--profile", "min", "--format", "demo", "-o", "-"])
    assert json.loads(to_stdout.stdout)["clusters"][0]["name"] == "workbench"


def test_command_without_a_format_refuses_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("BENCH_EXPORT_FORMAT", raising=False)
    monkeypatch.setattr(export, "LOCAL_FORMAT_FILE", tmp_path / "none")
    monkeypatch.setattr(export.format_id, "__defaults__", (None, None, tmp_path / "none"))
    r = runner.invoke(cli.app, ["export", "profile", "--profile", "min", "-o", "-"])
    assert r.exit_code == 1 and "cannot export" in r.output and "--format" in r.output


def fake_docker(*services):
    class R:
        stdout = "\n".join(services)
    return lambda cmd: R()


def test_running_lists_only_what_docker_reports(tmp_path):
    none = tmp_path / "none.yaml"
    assert export.running(SIM, fake_docker(), none) is None
    sim, services = export.running(SIM, fake_docker("login", "slurmctld", "cn001"), none)
    assert services == {"login", "slurmctld", "cn001"} and sim["compute"]["count"] == SIM["compute"]["count"]


def test_live_profile_leaves_out_stopped_parts_and_nodes(tmp_path):
    sim, services = export.running(SIM, fake_docker("login", "slurmctld", "cn001", "gpu001"), tmp_path / "none.yaml")
    body = export.build(sim, services, include_bmc=True, live=True)
    assert body["clusters"][0]["jira"] is None and body["clusters"][0]["grafana"] is None
    assert "bmc" not in body  # bmc-farm is not running
    sim, services = export.running(SIM, fake_docker("login", "bmc-farm", "cn001"), tmp_path / "none.yaml")
    assert [t["node"] for t in export.build(sim, services, include_bmc=True, live=True)["bmc"]] == ["cn001"]


def test_command_follows_the_running_containers_and_falls_back_with_a_note(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(export.stack, "_project_services", lambda runner: ["login", "slurmctld"])
    r = runner.invoke(cli.app, ["export", "profile", "--format", "demo", "-o", "-"])
    cluster = json.loads(r.stdout)["clusters"][0]
    assert r.exit_code == 0 and cluster["jira"] is None and cluster["scheduler"]["kind"] == "slurm"
    monkeypatch.setattr(export.stack, "_project_services", lambda runner: [])
    monkeypatch.setattr(export.workbench, "SAVED", tmp_path / "none.yaml")
    monkeypatch.setattr(export.stack, "current_profile", lambda: "min")
    r = runner.invoke(cli.app, ["export", "profile", "--format", "demo", "-o", "-"])
    assert r.exit_code == 0 and "no containers are running" in r.output
    monkeypatch.setattr(export.stack, "current_profile", lambda: "workbench")  # a selection was built, its file is gone
    r = runner.invoke(cli.app, ["export", "profile", "--format", "demo", "-o", "-"])
    assert r.exit_code == 1 and "no saved workbench" in r.output
    r = runner.invoke(cli.app, ["export", "profile", "--format", "demo", "--configured", "-o", "-"])
    assert "no containers" not in r.output


def test_warns_when_accounting_is_missing_so_sacct_would_fail():
    assert "sacct" in export.warnings({"login", "slurmctld"})[0]
    assert export.warnings({"login", "slurmctld", "slurmdbd"}) == [] and export.warnings({"login"}) == []
    r = runner.invoke(cli.app, ["export", "profile", "--profile", "min", "--format", "demo", "-o", "-"])
    assert "warning:" in r.output and "--with accounting" in r.output


def test_start_passes_parts_to_the_launcher_and_refuses_a_mix(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_launcher", lambda *a: seen.append(a))
    monkeypatch.setattr(cli, "_offer_completion", lambda: None)
    assert runner.invoke(cli.app, ["start", "--with", "accounting", "--without", "jira"]).exit_code == 0
    assert seen[-1] == ("start", "--with", "accounting", "--without", "jira")
    assert runner.invoke(cli.app, ["start"]).exit_code == 0 and seen[-1] == ("start", "--profile", "min")
    assert runner.invoke(cli.app, ["start", "--profile", "std", "--with", "load"]).exit_code == 1


def test_set_node_marks_a_node_down_or_back_through_scontrol(monkeypatch):
    ran = []
    monkeypatch.setattr(cli.stack, "_run", lambda cmd: ran.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    r = runner.invoke(cli.app, ["set", "node", "cn001", "down"])
    assert r.exit_code == 0 and "state=down" in ran[-1] and "nodename=cn001" in ran[-1]
    assert runner.invoke(cli.app, ["set", "node", "cn001", "sideways"]).exit_code == 1


def test_bmc_targets_use_the_node_name_through_the_login_node_and_loopback_when_direct():
    via_login = export.build(SIM, ALL, include_bmc=True)["bmc"]
    assert [t["address"] for t in via_login][:2] == ["bmc-cn001", "bmc-cn002"] and {t["port"] for t in via_login} == {8443}
    direct = export.build(SIM, ALL, include_bmc=True, bmc_via="direct")["bmc"]
    assert {t["address"] for t in direct} == {"127.0.0.1"} and [t["node"] for t in direct] == [t["node"] for t in via_login]
    with pytest.raises(export.ExportError, match="bad --bmc-via"):
        export.build(SIM, ALL, include_bmc=True, bmc_via="tunnel")
