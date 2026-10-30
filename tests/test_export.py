"""benchctl export profile: shape, omissions, the locally-read format identifier, and no secrets (issue 91)."""
import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from simctl import cli, export

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
    assert json.loads(to_stdout.output)["clusters"][0]["name"] == "workbench"


def test_command_without_a_format_refuses_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("BENCH_EXPORT_FORMAT", raising=False)
    monkeypatch.setattr(export, "LOCAL_FORMAT_FILE", tmp_path / "none")
    monkeypatch.setattr(export.format_id, "__defaults__", (None, None, tmp_path / "none"))
    r = runner.invoke(cli.app, ["export", "profile", "--profile", "min", "-o", "-"])
    assert r.exit_code == 1 and "cannot export" in r.output and "--format" in r.output
