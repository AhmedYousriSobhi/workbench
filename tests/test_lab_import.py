"""Config lab import (issue 550): parsers, scrubber, refusal of a leftover secret, the store and the CLI. Synthetic fixtures only."""
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, lab
from benchctl import lab_import as li

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def fixture(tmp_path) -> Path:
    d = tmp_path / "export"
    shutil.copytree(FIX, d)
    return d


def test_hostlist_expands_ranges_widths_and_lists():
    assert li.expand_hostlist("gpu[001-003],cpu1") == ["gpu001", "gpu002", "gpu003", "cpu1"]
    assert li.expand_hostlist("n[8-10]") == ["n8", "n9", "n10"]
    assert li.expand_hostlist("a[1,3]-x") == ["a1-x", "a3-x"]
    with pytest.raises(li.ImportError_):
        li.expand_hostlist("n[9-1]")


def test_slurm_conf_keys_nodes_partitions_and_continuations():
    d = li.parse_slurm_conf("A=1  # c\nNodeName=n[1-2] CPUs=4 \\\n  RealMemory=8\nPartitionName=p Nodes=ALL\nSwitchName=s Nodes=n1\n")
    assert d["keys"] == {"A": "1"}
    assert d["nodes"] == [{"NodeName": "n[1-2]", "CPUs": "4", "RealMemory": "8"}]
    assert d["partitions"][0]["PartitionName"] == "p" and d["other"][0]["SwitchName"] == "s"
    with pytest.raises(li.ImportError_, match="line 1"):
        li.parse_slurm_conf("not a key value line\n")


def test_topology_gres_and_dump_parsers_reject_malformed_input():
    assert li.parse_topology("SwitchName=a Nodes=n1\n")[0]["SwitchName"] == "a"
    with pytest.raises(li.ImportError_):
        li.parse_topology("Nodes=n1\n")
    assert li.parse_gres("NodeName=g1 Name=gpu Count=4\n")[0]["Count"] == "4"
    with pytest.raises(li.ImportError_):
        li.parse_gres("NodeName=g1 Count=4\n")
    d = li.parse_sacctmgr_dump((FIX / "sacctmgr.dump").read_text())
    assert d["cluster"] == "prodsim" and [a["name"] for a in d["accounts"]] == ["physics", "ml"]
    assert {u["name"]: u["opts"]["DefaultAccount"] for u in d["users"]}["u1"] == "physics"
    with pytest.raises(li.ImportError_, match="line 1"):
        li.parse_sacctmgr_dump("garbage\n")


def test_qos_and_trace_tables_need_their_columns_and_drop_step_rows():
    q = li.parse_qos((FIX / "qos.txt").read_text())
    assert q[1]["Name"] == "high" and q[1]["Preempt"] == "normal"
    t = li.parse_trace((FIX / "trace.sacct").read_text())
    assert [j["JobID"] for j in t["jobs"]] == ["1", "2", "3"] and t["steps_dropped"] == 1
    with pytest.raises(li.ImportError_, match="lacks the column"):
        li.parse_trace("JobID|User\n1|a\n")
    with pytest.raises(li.ImportError_, match="AllocTRES or ReqTRES"):
        li.parse_trace("JobID|User|Account|Partition|Submit|Elapsed|State\n")
    with pytest.raises(li.ImportError_, match="columns"):
        li.parse_trace("JobID|User|Account|Partition|Submit|Elapsed|State|ReqTRES\n1|a|b\n")


def test_kubernetes_keeps_the_allow_list_and_drops_secrets():
    kept, dropped = li.parse_k8s((FIX / "k8s" / "policy.yaml").read_text())
    assert [o["kind"] for o in kept] == ["Namespace", "PriorityClass", "ResourceQuota"]
    assert dropped == {"Secret": 1, "Deployment": 1}
    assert "password" not in yaml.safe_dump_all(kept)
    kept, _ = li.parse_k8s("kind: List\nitems:\n- {kind: Role, metadata: {name: r}}\n")
    assert len(kept) == 1
    with pytest.raises(li.ImportError_, match="not valid YAML"):
        li.parse_k8s("a: [")


def test_rules_and_teleport_shapes():
    assert li.parse_rules((FIX / "rules" / "alerts.yml").read_text())[0]["name"] == "nodes"
    for bad in ("", "groups: []", "groups: [{name: g, rules: [{alert: A}]}]"):
        with pytest.raises(li.ImportError_):
            li.parse_rules(bad)
    roles, dropped = li.parse_teleport((FIX / "teleport" / "roles.yaml").read_text())
    assert [r["metadata"]["name"] for r in roles] == ["hpc-user"] and dropped == {"token": 1}


def test_scrubber_replaces_known_secrets_and_never_the_policy_keys():
    out, n = li.scrub_slurm_conf((FIX / "slurm.conf").read_text())
    assert n == 4  # AccountingStoragePass, StoragePass, AuthAltParameters, AuthInfo
    assert "hunter2" not in out and "another-not-real" not in out and "jwt_hs256" not in out and "munge.socket" not in out
    assert "PriorityWeightQOS=1000" in out and "AccountingStoragePass=<scrubbed>" in out
    assert li.residual_secrets({"slurm.conf": out}) == []
    assert li.scrub_slurm_conf("X=a StoragePass=<scrubbed>")[1] == 0


@pytest.mark.parametrize("line,rule", [
    ("token: " + "ghp_" + "a" * 24, "github-token"),
    ("x " + "AKIA" + "A" * 16, "aws-key"),
    ("-----BEGIN RSA PRIVATE " + "KEY-----", "private-key"),
    ("url: https://bob:" + "pw123@host/x", "url-credential"),
    ("db_password = s3cret", "secret-assignment"),
    ("api_key: abcdef", "secret-assignment"),
])
def test_residual_scan_names_file_line_and_rule_but_never_the_value(line, rule):
    hits = li.residual_secrets({"f.yaml": "ok: 1\n" + line + "\n"})
    assert f"f.yaml:2 [{rule}]" in hits
    assert all(not re.search(r"s3cret|pw123|abcdef", h) for h in hits)


def test_token_rules_match_check_privacy_script():
    script = (ROOT / "scripts" / "check-privacy.sh").read_text()
    for name, rx in li.TOKEN_RULES:
        if name in ("api-key", "private-key", "url-credential"):
            continue  # written differently in shell (escaping of the rule's own text)
        assert rx in script, name
    assert "sk-[A-Za-z0-9_-]{32,}" in script


def test_import_stores_scrubbed_files_and_a_manifest(tmp_path):
    root = tmp_path / "lab"
    m = lab.import_lab("prod-a", fixture(tmp_path), root=root, sim=SIM)
    base = root / "prod-a" / "baseline"
    assert (base / "slurm.conf").is_file() and (base / "k8s" / "policy.yaml").is_file()
    stored = "\n".join(p.read_text() for p in base.rglob("*") if p.is_file())
    assert "hunter2" not in stored and "not-a-real-password" not in stored and "bm90LWE" not in stored and "join-token" not in stored
    disk = json.loads((root / "prod-a" / "manifest.json").read_text())
    assert disk == m
    assert m["versions"] == {"slurm": "25.11.9", "kubernetes": "v1.36.1", "prometheus": "2.53.0", "teleport": "18.11.8"}
    counts = {f["file"]: f for f in m["files"]}
    assert counts["slurm.conf"]["nodes"] == 6 and counts["slurm.conf"]["partitions"] == 2
    assert counts["trace.sacct"]["jobs"] == 3 and counts["rules/alerts.test.yml"]["kind"] == "rule-tests"
    assert m["dropped_objects"] == {"Secret": 1, "Deployment": 1, "token": 1}
    assert m["scrubbed"] == [{"file": "slurm.conf", "values": 4}]
    assert "Alertmanager routing" in m["not_tested"] and m["version_match"]["kubernetes"] == "same"
    assert m["version_match"]["slurm"] == "differs" and m["version_match"]["prometheus"] == "unknown"
    assert lab.names(root) == ["prod-a"]
    assert "indicative" in lab.summary(m)


def test_an_unscrubbed_secret_refuses_the_import_with_one_message_and_stores_nothing(tmp_path):
    d = fixture(tmp_path)
    (d / "rules" / "alerts.yml").write_text((d / "rules" / "alerts.yml").read_text() + "        annotations: {db_password: hunter2xyz}\n")
    with pytest.raises(lab.LabError) as e:
        lab.import_lab("x", d, root=tmp_path / "lab")
    msg = str(e.value)
    assert "rules/alerts.yml:" in msg and "secret-assignment" in msg and "hunter2xyz" not in msg and "\n" not in msg
    assert not (tmp_path / "lab").exists()


def test_name_source_and_replace_rules(tmp_path):
    root = tmp_path / "lab"
    for bad in ("Prod", "-x", "a b", "x" * 42, ""):
        with pytest.raises(lab.LabError, match="lab name"):
            lab.import_lab(bad, FIX, root=root)
    with pytest.raises(lab.LabError, match="no such folder"):
        lab.import_lab("a", tmp_path / "nope", root=root)
    (tmp_path / "empty").mkdir()
    with pytest.raises(lab.LabError, match="no recognised file"):
        lab.import_lab("a", tmp_path / "empty", root=root)
    lab.import_lab("a", FIX, root=root)
    with pytest.raises(lab.LabError, match="--replace"):
        lab.import_lab("a", FIX, root=root)
    lab.import_lab("a", FIX, root=root, replace=True)
    assert lab.names(root) == ["a"] and not list(root.glob("*.tmp"))


def test_versions_file_is_checked(tmp_path):
    d = fixture(tmp_path)
    (d / "versions.yaml").write_text("slurm: 25.11.9\nmunge: 1\n")
    with pytest.raises(lab.LabError, match="unknown key"):
        lab.import_lab("a", d, root=tmp_path / "lab")
    (d / "versions.yaml").write_text("- a\n")
    with pytest.raises(lab.LabError, match="mapping"):
        lab.import_lab("a", d, root=tmp_path / "lab")
    (d / "versions.yaml").unlink()
    assert lab.import_lab("a", d, root=tmp_path / "lab")["versions"]["slurm"] is None


def test_other_files_are_reported_as_ignored(tmp_path):
    d = fixture(tmp_path)
    (d / "cgroup.conf").write_text("ConstrainCores=yes\n")
    assert lab.import_lab("a", d, root=tmp_path / "lab")["ignored_files"] == ["cgroup.conf"]


def test_cli_import_prints_a_summary_and_one_line_on_refusal(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(lab, "ROOT", tmp_path / "lab")
    r = CliRunner().invoke(cli.app, ["import", "lab", "demo", "--from", str(FIX)])
    assert r.exit_code == 0 and "imported lab demo" in r.output and "not tested:" in r.output
    r = CliRunner().invoke(cli.app, ["import", "lab", "demo", "--from", str(FIX)])
    assert r.exit_code == 1 and "--replace" in r.output
    r = CliRunner().invoke(cli.app, ["import", "lab", "demo", "--from", str(tmp_path / "missing")])
    assert r.exit_code == 1 and "no such folder" in r.output
    r = CliRunner().invoke(cli.app, ["import", "lab", "demo"])
    assert r.exit_code == 2


def test_lab_store_is_refused_by_the_privacy_script_when_staged():
    assert "compose/generated" in (ROOT / "scripts" / "check-privacy.sh").read_text()


def test_a_real_sacctmgr_dump_starts_with_qos_records_that_are_not_accounts():
    """Captured (issue 778): `sacctmgr dump` writes `QOS - 'name':...` lines before the cluster; the QOS come from `sacctmgr show qos`."""
    text = "QOS - 'normal':Description='Normal QOS default'\nCluster - 'c1':Fairshare=1\nParent - 'root'\nAccount - 'a':Description='a':Fairshare=1\n"
    got = li.parse_sacctmgr_dump(text)
    assert got["cluster"] == "c1" and [a["name"] for a in got["accounts"]] == ["a"]
