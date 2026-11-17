"""Config lab candidate, diff, apply and remove (issue 553): hermetic, a fake cluster behind the injectable runner. Synthetic fixtures only."""
import json
import re
import shutil
import threading
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, lab, lab_apply, lab_candidate, lab_diff, slurmconf
from benchctl.lab import LabError
from governor import control
from governor.core import Governor
from governor.ladder import Ladder
from governor.signals import GB, FileSampler

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
BASE_CONF = """ClusterName=workbench
SelectType=select/cons_tres
PriorityType=priority/multifactor
PriorityWeightQOS=500
PreemptType=preempt/none
PreemptMode=OFF
SchedulerParameters=bf_interval=60
GresTypes=gpu
SlurmctldTimeout=120
NodeName=cn001 CPUs=4
PartitionName=cpu Nodes=cn001 Default=YES
"""
PRE_DUMP = """Cluster - 'workbench':Fairshare=1
Parent - 'root'
Account - 'sim':Description='sim':Organization='sim':Fairshare=1
Parent - 'sim'
User - 'sim1':DefaultAccount='sim':Fairshare=1
"""


class Cluster:
    """Just enough of slurmctld, slurmdbd, Kubernetes, Teleport and Prometheus for apply and remove to be observed."""

    def __init__(self, tmp: Path):
        self.slurm_dir = tmp / "slurm"
        self.slurm_dir.mkdir()
        (self.slurm_dir / "slurm.conf").write_text(BASE_CONF)
        self.cfg = self.conf_values()
        self.restarts = self.reconfigures = 0
        self.ignore_reconfigure = False
        self.records = lab_apply.dump_records(PRE_DUMP)
        self.qos = {"normal": {"Name": "normal", "Priority": "0", "GraceTime": "00:00:00"}}
        self.k8s: dict[str, dict] = {}
        self.roles: dict[str, dict] = {}
        self.calls: list[str] = []
        self.reload_ok = "1"
        self.refuse_delete: set[str] = set()
        self.services = {"slurmctld", "slurmdbd", "k8s-apiserver", "teleport", "prometheus"}

    def conf_values(self) -> dict[str, str]:
        return dict(lab_apply.conf_keys((self.slurm_dir / "slurm.conf").read_text()))

    def reload(self) -> None:
        self.cfg = self.conf_values()

    def restart(self) -> bool:
        self.restarts += 1
        self.reload()
        return True

    def dump(self) -> str:
        out, parent = ["Cluster - 'workbench':Fairshare=1"], None
        for key, line in self.records.items():
            p = key.split("/")[1]
            if p != parent:
                out.append(f"Parent - '{p}'")
                parent = p
            out.append(line)
        return "\n".join(out) + "\n"

    def qos_text(self) -> str:
        return "".join("|".join(r.get(f, "") for f in ["Name", *lab_apply.QOS_FIELDS]) + "\n" for r in self.qos.values())

    def run(self, cmd, input=None):
        text = " ".join(cmd)
        self.calls.append(text)
        ok = lambda out="": type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
        bad = lambda err="no": type("R", (), {"returncode": 1, "stdout": "", "stderr": err})()
        if "kill -s HUP prometheus" in text:
            return ok()
        if text.endswith("scontrol show config"):
            return ok("".join(f"{k:<24}= {v}\n" for k, v in self.cfg.items()) + "BOOT_TIME = 12:00\n")
        if text.endswith("scontrol reconfigure"):
            self.reconfigures += 1
            if not self.ignore_reconfigure:
                self.reload()
            return ok()
        if "sacctmgr -i dump" in text:
            return ok(self.dump())
        if "show qos" in text:
            return ok(self.qos_text())
        if "sacctmgr -i load" in text:
            for k, v in lab_apply.dump_records(input).items():
                self.records[k] = v
            return ok()
        if "sacctmgr -i add qos" in text:
            n = cmd[-1]
            if n in self.qos:
                return bad("exists")
            self.qos[n] = {"Name": n}
            return ok()
        if "sacctmgr -i modify qos" in text:
            n, sets = cmd[cmd.index("qos") + 1], cmd[cmd.index("set") + 1:]
            for s in sets:
                k, v = s.split("=", 1)
                self.qos[n][k] = "" if v == "-1" else v
            return ok()
        if "sacctmgr -i delete qos" in text:
            if cmd[-1] in self.refuse_delete:
                return bad("busy")
            self.qos.pop(cmd[-1], None)
            return ok()
        m = re.search(r"sacctmgr -i delete (user|account) (\S+)", text)
        if m:
            kind, n = m.group(1).capitalize(), m.group(2)
            if n in self.refuse_delete:
                return bad("busy")
            for k in [k for k in self.records if k.startswith(kind + "/") and k.endswith("/" + n)]:
                del self.records[k]
            return ok()
        if "tctl get role/" in text:
            n = cmd[-2].split("/")[1]
            return ok(json.dumps([self.roles[n]])) if n in self.roles else bad("not found")
        if "tctl create" in text:
            for d in yaml.safe_load_all(input):
                self.roles[d["metadata"]["name"]] = d
            return ok()
        if "tctl rm role/" in text:
            self.roles.pop(cmd[-1].split("/")[1], None)
            return ok()
        raise AssertionError(f"unexpected command: {text}")

    def kubectl(self, args, stdin):
        args = list(args)
        ok = lambda out="": type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
        if args[0] == "get":
            key = f"{args[1]}/{args[2]}"
            return ok(json.dumps(self.k8s[key]) if key in self.k8s else "")
        if args[0] == "apply":
            for d in yaml.safe_load_all(stdin):
                self.k8s[f"{d['kind'].lower()}/{d['metadata']['name']}"] = d
            return ok()
        if args[0] == "delete":
            sel = args[args.index("-l") + 1].split("=")[1]
            for k in [k for k, d in self.k8s.items() if d["metadata"].get("labels", {}).get(lab_apply.LABEL) == sel]:
                del self.k8s[k]
            return ok()
        raise AssertionError(args)

    def http(self, url):
        return f"# HELP prometheus_config_last_reload_successful Whether the last config reload succeeded.\nprometheus_config_last_reload_successful {self.reload_ok}\n"


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "lab"
    export = tmp_path / "export"
    shutil.copytree(FIX, export)
    lab.import_lab("prod-a", export, root=root)
    c = Cluster(tmp_path)
    env = lab_apply.Env(root=root, slurm_dir=c.slurm_dir, prom_dir=tmp_path / "gen", sim={}, runner=c.run, running=lambda s: s in c.services,
                        kubectl=c.kubectl, http_get=c.http, rules_check=lambda paths: [], restart_ctld=c.restart, sleep=lambda s: None, say=lambda s: None)
    (c.slurm_dir.parent / "gen").mkdir(exist_ok=True)
    (root / "prod-a" / "baseline" / "sacctmgr.dump").write_text(PRE_DUMP.replace("workbench", "prodsim") + "Account - 'ml':Description='ml':Organization='sci':Fairshare=20\nParent - 'ml'\nUser - 'u2':DefaultAccount='ml':Fairshare=1\n")
    conf = root / "prod-a" / "baseline" / "slurm.conf"
    conf.write_text(conf.read_text() + "SiteOnlyKey=1\n")  # a key in no class: reported, never applied
    return env, c, root


# ---------------------------------------------------------------- candidate and diff

def test_candidate_is_a_copy_of_the_baseline_that_is_not_overwritten(world):
    _env, _c, root = world
    with pytest.raises(LabError, match="no candidate"):
        lab_candidate.effective("prod-a", "candidate", root)
    files = lab_candidate.init("prod-a", root)
    assert "slurm.conf" in files and "k8s/policy.yaml" in files
    with pytest.raises(LabError, match="already has a candidate"):
        lab_candidate.init("prod-a", root)
    with pytest.raises(LabError, match="side must be"):
        lab_candidate.check_side("both")
    assert lab_candidate.init("prod-a", root, replace=True)


def edit_candidate(root, old, new):
    lab_candidate.init("prod-a", root, replace=True)
    f = root / "prod-a" / "candidate" / "slurm.conf"
    f.write_text(f.read_text().replace(old, new))


def test_diff_classifies_each_key_and_says_restart_or_reconfigure(world):
    _env, _c, root = world
    edit_candidate(root, "PriorityWeightQOS=1000", "PriorityWeightQOS=2000")
    entries = lab_diff.diff("prod-a", root)
    assert [(e["key"], e["note"]) for e in entries] == [("PriorityWeightQOS", "live")]
    assert lab_diff.restart_keys(entries) == []
    assert "reconfigures (no restart)" in lab_diff.text("prod-a", entries)
    edit_candidate(root, "PreemptMode=REQUEUE", "PreemptMode=CANCEL\nPreemptType=preempt/partition_prio\nMyNewKey=1\nSlurmctldHost=other\nPriorityDecayHalfLife=7-0")
    notes = {e["key"]: e["note"] for e in lab_diff.diff("prod-a", root)}
    assert notes["PreemptMode"] == "live" and notes["PreemptType"] == "restart"
    assert notes["MyNewKey"] == "not applied: unknown" and notes["SlurmctldHost"].startswith("not tested: lab-owned")
    assert notes["PriorityDecayHalfLife"] == "live (time unscaled)"
    entries = lab_diff.diff("prod-a", root)
    assert lab_diff.restart_keys(entries) == ["PreemptType"]  # an unknown key never restarts: it is not applied
    assert "restarts (PreemptType)" in lab_diff.text("prod-a", entries)


def test_diff_lines_of_other_files_and_structure_notes(world):
    _env, _c, root = world
    lab_candidate.init("prod-a", root, replace=True)
    cand = root / "prod-a" / "candidate"
    (cand / "rules" / "alerts.yml").write_text((cand / "rules" / "alerts.yml").read_text() + "# one more\n")
    (cand / "slurm.conf").write_text((cand / "slurm.conf").read_text().replace("MaxTime=2-00:00:00", "MaxTime=1-00:00:00"))
    (cand / "gres.conf").write_text("NodeName=g1 Name=gpu Count=2\n")
    (cand / "qos.txt").unlink()
    entries = lab_diff.diff("prod-a", root)
    by_file = {}
    for e in entries:
        by_file.setdefault(e["file"], []).append(e)
    assert by_file["rules/alerts.yml"][0]["note"] == "live" and by_file["rules/alerts.yml"][0]["op"] == "added"
    assert all(e["note"].startswith("not applied: structure") for e in by_file["slurm.conf"])
    assert by_file["gres.conf"][0]["note"].startswith("not applied: structure")
    assert lab_diff.text("prod-a", [])  # equal text
    with pytest.raises(LabError):
        (cand / "slurm.conf").write_text("not a key\n")
        lab_diff.diff("prod-a", root)


# ---------------------------------------------------------------- overlay

def test_overlay_replaces_in_place_adds_a_block_and_strips_back_exactly():
    new = lab_apply.overlay_text(BASE_CONF, {"PriorityWeightQOS": "1000", "NewKey": "a b", "SelectType": "select/cons_tres"}, "x")
    assert "PriorityWeightQOS=1000 # lab-was 500" in new and 'NewKey="a b"' in new and "BEGIN lab x" in new
    assert "SelectType=select/cons_tres\n" in new and "lab-was" not in new.split("SelectType")[1].split("\n")[0]
    assert lab_apply.strip_overlay(new) == BASE_CONF
    again = lab_apply.overlay_text(new, {"PriorityWeightQOS": "1500"}, "x")  # re-overlay starts from the stripped text
    assert "1000" not in again and "NewKey" not in again and lab_apply.strip_overlay(again) == BASE_CONF


def test_slurmconf_writer_reapplies_the_marker_and_up_says_so(tmp_path):
    gen = tmp_path / "generated"
    (gen / "lab").mkdir(parents=True)
    lab_apply.write_marker({"name": "prod-a", "keys": {"PriorityWeightQOS": "7"}}, gen / "lab")
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    slurmconf.write(sim, gen / "slurm")
    assert "PriorityWeightQOS=7" in (gen / "slurm" / "slurm.conf").read_text()
    said = []
    lab_apply.note_active(said.append, gen / "lab")
    assert said == ["lab prod-a is active: its settings were re-applied"]
    (gen / "lab" / "active.json").unlink()
    slurmconf.write(sim, gen / "slurm")
    assert "PriorityWeightQOS=7" not in (gen / "slurm" / "slurm.conf").read_text()
    lab_apply.note_active(said.append, gen / "lab")
    assert len(said) == 1


# ---------------------------------------------------------------- apply

def test_apply_baseline_applies_policy_keys_restarts_for_restart_class_and_verifies(world):
    env, c, root = world
    before = dict(c.cfg)
    res = lab_apply.apply("prod-a", "baseline", env)
    out = "\n".join(res["lines"])
    assert c.restarts == 1 and c.reconfigures == 0 and "slurmctld restarts: PreemptType" in out
    assert c.cfg["PriorityWeightQOS"] == "1000" and c.cfg["PreemptMode"] == "REQUEUE"
    assert "not applied: unknown: SiteOnlyKey" in out and "lab-owned" in out and "times are unscaled" in out
    m = lab_apply.read_marker(root)
    assert m["name"] == "prod-a" and m["side"] == "baseline" and m["time_scale"] == 1.0 and m["keys"]["PriorityWeightQOS"] == "1000"
    assert "hunter2" not in json.dumps(m) and "scrubbed" not in json.dumps(m["keys"]) and "SlurmctldHost" not in m["keys"] and "SiteOnlyKey" not in m["keys"]
    assert (root / "prod-a" / "render" / "pre-lab-config.txt").read_text().count("PriorityWeightQOS") == 1
    assert "PriorityWeightQOS=1000 # lab-was 500" in (c.slurm_dir / "slurm.conf").read_text()
    assert before["PriorityWeightQOS"] == "500"
    assert lab_apply.status_block(root) == {"name": "prod-a", "side": "baseline", "time_scale": 1.0, "state": "applied"}


def test_apply_candidate_with_only_a_live_change_reconfigures(world):
    env, c, root = world
    lab_apply.apply("prod-a", "baseline", env)
    edit_candidate(root, "PriorityWeightQOS=1000", "PriorityWeightQOS=2000")
    c.restarts = 0
    res = lab_apply.apply("prod-a", "candidate", env)
    assert c.restarts == 0 and c.reconfigures == 1 and "reconfigured (no restart)" in "\n".join(res["lines"])
    assert c.cfg["PriorityWeightQOS"] == "2000" and lab_apply.read_marker(root)["side"] == "candidate"
    assert (root / "prod-a" / "render" / "pre-lab-config.txt").read_text().count("PriorityWeightQOS") == 1  # the first snapshot is kept


def test_a_key_that_falls_away_returns_to_its_base_value(world):
    env, c, root = world
    lab_apply.apply("prod-a", "baseline", env)
    edit_candidate(root, "PreemptMode=REQUEUE\n", "")
    c.restarts = 0
    lab_apply.apply("prod-a", "candidate", env)
    assert c.cfg["PreemptMode"] == "OFF" and c.reconfigures == 1 and c.restarts == 0


def test_verify_catches_a_mismatch_and_keeps_the_marker(world):
    env, c, root = world
    lab_apply.apply("prod-a", "baseline", env)
    edit_candidate(root, "PriorityWeightQOS=1000", "PriorityWeightQOS=3000")
    c.ignore_reconfigure = True  # scontrol keeps the old value, as when the file is not the one slurmctld reads
    with pytest.raises(LabError, match=r"differs from the rendered settings: PriorityWeightQOS wanted 3000, live 1000"):
        lab_apply.apply("prod-a", "candidate", env)
    assert lab_apply.read_marker(root)["name"] == "prod-a"


def test_same_value_normalises_case_numbers_and_times():
    same = lab_apply.same_value
    assert same("REQUEUE", "requeue") and same("1000", "1000.0") and same("7-0", "7-00:00:00") and same('"a"', "a")
    assert not same("1000", "2000") and not same("preempt/qos", "preempt/none")
    bad, unseen = lab_apply.verify({"A": "1", "B": "2", "C": "3"}, {"a": "1", "B": "9"})
    assert bad == [("B", "2", "9")] and unseen == ["C"]


def test_apply_refuses_before_changing_anything(world):
    env, c, root = world
    conf = (c.slurm_dir / "slurm.conf").read_text()
    c.services.discard("slurmctld")
    with pytest.raises(LabError, match="cluster is not running"):
        lab_apply.apply("prod-a", "baseline", env)
    c.services.add("slurmctld")
    with pytest.raises(LabError, match="no candidate"):
        lab_apply.apply("prod-a", "candidate", env)
    with pytest.raises(LabError, match="lab name"):
        lab_apply.apply("Bad Name", "baseline", env)
    with pytest.raises(LabError, match="side must be"):
        lab_apply.apply("prod-a", "both", env)
    c.k8s["priorityclass/batch-low"] = {"metadata": {"name": "batch-low"}}  # made by someone else: no lab label
    with pytest.raises(LabError, match="PriorityClass/batch-low already exists and was not made by lab prod-a"):
        lab_apply.apply("prod-a", "baseline", env)
    del c.k8s["priorityclass/batch-low"]
    c.roles["hpc-user"] = {"metadata": {"name": "hpc-user"}}
    with pytest.raises(LabError, match="Teleport role hpc-user already exists"):
        lab_apply.apply("prod-a", "baseline", env)
    assert (c.slurm_dir / "slurm.conf").read_text() == conf and lab_apply.read_marker(root) is None and c.restarts == 0
    other = lab_apply.Env(**env.__dict__)
    lab.import_lab("other", FIX, root=root)
    del c.roles["hpc-user"]
    lab_apply.apply("prod-a", "baseline", env)
    with pytest.raises(LabError, match="lab prod-a is active: remove it first"):
        lab_apply.apply("other", "baseline", other)


def test_apply_refuses_a_secret_in_the_candidate(world):
    env, _c, root = world
    edit_candidate(root, "PriorityWeightQOS=1000", "PriorityWeightQOS=1000\nStoragePass=hunter2hunter2")
    with pytest.raises(LabError, match="secret"):
        lab_apply.apply("prod-a", "candidate", env)
    assert lab_apply.read_marker(root) is None


def test_rules_are_checked_then_loaded_and_the_reload_is_confirmed(world):
    env, c, root = world
    seen = []
    env.rules_check = lambda paths: seen.append([p.read_text()[:5] for p in paths]) or ["rule x is broken"]
    with pytest.raises(LabError, match="promtool refuses the rules: rule x is broken"):
        lab_apply.apply("prod-a", "baseline", env)
    assert seen and not list((env.prom_dir / lab_apply.RULES_REL).glob("*.yml")) and lab_apply.read_marker(root) is None
    env.rules_check = lambda paths: []
    lab_apply.apply("prod-a", "baseline", env)
    files = sorted(p.name for p in (env.prom_dir / lab_apply.RULES_REL).glob("*.yml"))
    assert files == ["prod-a-alerts.yml"]  # the .test.yml file is not loaded
    assert any("kill -s HUP prometheus" in call for call in c.calls)


def test_promtool_check_uses_the_validation_executor_and_reports_errors(tmp_path):
    f = tmp_path / "r.yml"
    f.write_text("groups: []\n")
    calls = []

    def ok(argv, stdin=None, timeout=60):
        calls.append(argv)
        return 0, "SUCCESS"

    assert lab_apply.promtool_check([f], ok) == [] and lab_apply.promtool_check([], ok) == []
    argv = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"] and "promtool" in argv and argv[-3:] == ["check", "rules", "/rules/r.yml"] and f"{tmp_path.resolve()}:/rules:ro" in argv
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    bad = lambda argv, stdin=None, timeout=60: (1, "Checking /rules/r.yml\n  FAILED:\n/rules/r.yml: 1:1: error: bad expr\n")
    assert lab_apply.promtool_check([f], bad) == ["FAILED:", "r.yml: 1:1: error: bad expr"]
    assert lab_apply.promtool_check([f], lambda argv, stdin=None, timeout=60: (127, "")) == ["promtool exit 127"]


def test_accounting_storage_type_is_lab_owned_never_applied_and_shown_so(world):
    from benchctl import lab_slurm
    assert lab_slurm.classify("AccountingStorageType") == "lab-owned"
    env, c, root = world
    conf = root / "prod-a" / "baseline" / "slurm.conf"
    assert "AccountingStorageType=accounting_storage/slurmdbd" in conf.read_text()
    res = lab_apply.apply("prod-a", "baseline", env)
    m = lab_apply.read_marker(root)
    assert "AccountingStorageType" not in m["keys"] and "AccountingStorageType" in m["lab_owned"]
    assert "AccountingStorageType" not in (c.slurm_dir / "slurm.conf").read_text()
    assert "slurmctld restarts: PreemptType" in "\n".join(res["lines"])
    lab_apply.remove("prod-a", env)
    edit_candidate(root, "AccountingStorageType=accounting_storage/slurmdbd", "AccountingStorageType=accounting_storage/none")
    notes = {e["key"]: e["note"] for e in lab_diff.diff("prod-a", root)}
    assert notes["AccountingStorageType"].startswith("not tested: lab-owned") and lab_diff.restart_keys(lab_diff.diff("prod-a", root)) == []


def test_a_failed_prometheus_reload_is_reported(world):
    env, c, _root = world
    c.reload_ok = "0"
    with pytest.raises(LabError, match="prometheus_config_last_reload_successful is not 1"):
        lab_apply.apply("prod-a", "baseline", env)


def test_kubernetes_objects_are_labelled_and_teleport_roles_created(world):
    env, c, root = world
    res = lab_apply.apply("prod-a", "baseline", env)
    assert {k for k in c.k8s} == {"namespace/team-a", "priorityclass/batch-low", "resourcequota/gpu-quota"} or "secret" not in " ".join(c.k8s)
    assert all(d["metadata"]["labels"][lab_apply.LABEL] == "prod-a" for d in c.k8s.values())
    assert "secret/registry" not in c.k8s and list(c.roles) == ["hpc-user"]
    m = lab_apply.read_marker(root)
    assert m["created_objects"]["teleport_roles"] == ["hpc-user"] and len(m["created_objects"]["k8s"]) == len(c.k8s)
    assert any(line.startswith("kubernetes:") for line in res["lines"])
    lab_apply.apply("prod-a", "baseline", env)  # again: its own labelled objects are no clash


def test_parts_that_are_not_running_are_not_applied_and_said_so(world):
    env, c, root = world
    c.services -= {"slurmdbd", "k8s-apiserver", "teleport", "prometheus"}
    out = "\n".join(lab_apply.apply("prod-a", "baseline", env)["lines"])
    assert "not applied: QOS and accounts" in out and "Kubernetes is not running" in out and "Teleport is not running" in out
    assert "prometheus is not running" in out
    assert lab_apply.read_marker(root)["created_objects"]["sacctmgr"] is False


def test_partition_warnings(world):
    _env, _c, _root = world
    files = {"sacctmgr.dump": "User - 'u':DefaultAccount='a':Partition=ghost\n", "qos.txt": "Name|Partition\nq|cpu\n"}
    w = lab_apply.partition_warnings(files, BASE_CONF)
    assert w == ["warning: sacctmgr.dump names partition ghost, which the lab does not have (partitions come with the run)"]
    assert lab_apply.partition_warnings(files, "garbage") == []


# ---------------------------------------------------------------- accounting and remove

def test_accounting_is_applied_and_remove_restores_changes_and_deletes_only_what_the_lab_made(world):
    env, c, root = world
    c.records["Account/root/sim"] = c.records["Account/root/sim"].replace("Fairshare=1", "Fairshare=5")  # pre-lab value, to be changed by the load? same name in dump below
    pre_dump = c.dump()
    (root / "prod-a" / "baseline" / "sacctmgr.dump").write_text(
        "Cluster - 'prodsim'\nParent - 'root'\nAccount - 'sim':Description='sim':Organization='sim':Fairshare=99\n"
        "Account - 'ml':Description='ml':Organization='sci':Fairshare=20\nParent - 'ml'\nUser - 'u2':DefaultAccount='ml':Fairshare=1\n")
    (root / "prod-a" / "baseline" / "qos.txt").write_text("Name|Priority|GraceTime\nnormal|9|00:01:00\nhigh|1000|00:00:30\n")
    res = lab_apply.apply("prod-a", "baseline", env)
    assert "Fairshare=99" in c.records["Account/root/sim"] and "Account/root/ml" in c.records and "User/ml/u2" in c.records
    assert c.qos["high"]["Priority"] == "1000" and c.qos["normal"]["Priority"] == "9"
    assert any(line.startswith("accounting:") for line in res["lines"])
    pre_sacct = (root / "prod-a" / "render" / "sacctmgr.pre.dump").read_text()
    assert pre_sacct == pre_dump
    made, changed = lab_apply.record_delta(pre_sacct, (root / "prod-a" / "render" / "sacctmgr.post.dump").read_text())
    assert made == ["Account/root/ml", "User/ml/u2"] and changed == ["Account/root/sim"]
    out = "\n".join(lab_apply.remove("prod-a", env)["lines"])
    assert "Fairshare=5" in c.records["Account/root/sim"] and "Account/root/ml" not in c.records and "User/ml/u2" not in c.records
    assert "User/sim/sim1" in c.records  # present before the lab: never deleted
    assert "high" not in c.qos and c.qos["normal"]["Priority"] == "0" and c.qos["normal"]["GraceTime"] == "00:00:00"
    assert "2 account/user row(s) and 1 QOS removed" in out and "sacctmgr refused" not in out


def test_remove_lists_rows_sacctmgr_refuses_to_delete(world):
    env, c, root = world
    (root / "prod-a" / "baseline" / "qos.txt").write_text("Name|Priority\nhigh|1000\n")
    lab_apply.apply("prod-a", "baseline", env)
    c.refuse_delete = {"high", "u2"}
    out = "\n".join(lab_apply.remove("prod-a", env)["lines"])
    assert "sacctmgr refused: user u2, qos high" in out and lab_apply.read_marker(root) is None


def test_remove_returns_to_the_plain_cluster(world):
    env, c, root = world
    plain_conf, plain_cfg = (c.slurm_dir / "slurm.conf").read_text(), dict(c.cfg)
    lab_apply.apply("prod-a", "baseline", env)
    assert c.cfg != plain_cfg
    c.restarts = c.reconfigures = 0
    out = "\n".join(lab_apply.remove("prod-a", env)["lines"])
    assert (c.slurm_dir / "slurm.conf").read_text() == plain_conf and c.cfg == plain_cfg
    assert c.restarts == 1 and "back to the pre-lab settings" in out and lab_apply.read_marker(root) is None
    assert not c.k8s and not c.roles and not list((env.prom_dir / lab_apply.RULES_REL).glob("*.yml"))
    assert (root / "prod-a" / "manifest.json").is_file()  # the imported lab stays
    assert lab_apply.status_block(root) is None


def test_remove_refuses_when_the_accounting_the_lab_changed_is_not_running_and_keeps_the_marker(world):
    env, c, root = world
    lab_apply.apply("prod-a", "baseline", env)
    c.services.discard("slurmdbd")
    conf = (c.slurm_dir / "slurm.conf").read_text()
    with pytest.raises(LabError, match="made objects in accounting, which is not running.*stays active"):
        lab_apply.remove("prod-a", env)
    assert lab_apply.read_marker(root) and (c.slurm_dir / "slurm.conf").read_text() == conf and c.k8s and c.roles
    c.services.discard("k8s-apiserver")
    c.services.add("slurmdbd")
    with pytest.raises(LabError, match="Kubernetes, which is not running"):
        lab_apply.remove("prod-a", env)


def test_remove_of_an_inactive_lab_and_a_mismatch_after_remove(world):
    env, c, root = world
    with pytest.raises(LabError, match="not active"):
        lab_apply.remove("prod-a", env)
    lab_apply.apply("prod-a", "baseline", env)
    with pytest.raises(LabError, match=r"lab other is not active \(lab prod-a is\)"):
        lab_apply.remove("other", env)
    c.ignore_reconfigure = True
    c.restart = lambda: True  # the restart does not reload either
    env.restart_ctld = c.restart
    with pytest.raises(LabError, match="still differs from the pre-lab value"):
        lab_apply.remove("prod-a", env)
    assert lab_apply.read_marker(root)


# ---------------------------------------------------------------- execute, CLI, governor

def test_execute_saves_the_result_for_the_waiting_cli(world):
    env, _c, root = world
    said = []
    env.say = said.append
    assert lab_apply.execute("apply", "prod-a", "baseline", env)
    res = json.loads(lab_apply.result_path("prod-a", root).read_text())
    assert res["ok"] and res["op"] == "apply" and said == res["lines"]
    assert not lab_apply.execute("apply", "prod-a", "candidate", env)  # no candidate
    res = json.loads(lab_apply.result_path("prod-a", root).read_text())
    assert not res["ok"] and "no candidate" in res["error"]


def test_ask_governor_waits_for_the_change_and_prints_its_result(world, tmp_path):
    _env, _c, root = world
    lab_apply.result_path("prod-a", root).write_text(json.dumps({"ok": True, "lines": ["done a", "done b"], "error": None}))
    seq = iter([{"change": {"job": 3, "state": "running"}}, {"change": {"job": 3, "state": "done"}}])

    def request(req):
        return {"ok": True, "started": True, "job": 3} if req["cmd"] == "lab-apply" else next(seq)

    assert lab_apply.ask_governor({"cmd": "lab-apply"}, "prod-a", request, root, sleep=lambda s: None) == (True, ["done a", "done b"])
    assert lab_apply.ask_governor({"cmd": "x"}, "prod-a", lambda r: {"ok": False, "error": "refused at L2: stack changes are paused (GV-8)"}, root) == (False, ["refused at L2: stack changes are paused (GV-8)"])
    assert lab_apply.ask_governor({"cmd": "x"}, "prod-a", lambda r: {"ok": True, "dry_run": True}, root)[1] == ["the governor is in dry-run mode: nothing was changed"]

    def down(req):
        raise OSError("no socket")

    ok, lines = lab_apply.ask_governor({"cmd": "x"}, "prod-a", down, root)
    assert not ok and "governor is not reachable" in lines[0]


def test_cli_commands_read_the_lab_and_refuse_bad_input(world, monkeypatch, tmp_path):
    env, _c, root = world
    monkeypatch.setattr(lab, "ROOT", root)
    run = CliRunner().invoke
    r = run(cli.app, ["add", "candidate", "prod-a"])
    assert r.exit_code == 0 and "candidate of lab prod-a" in r.output
    assert run(cli.app, ["add", "candidate", "prod-a"]).exit_code == 1
    f = root / "prod-a" / "candidate" / "slurm.conf"
    f.write_text(f.read_text().replace("PriorityWeightQOS=1000", "PriorityWeightQOS=1"))
    r = run(cli.app, ["diff", "lab", "prod-a"])
    assert r.exit_code == 0 and "PriorityWeightQOS: 1000 -> 1  [live]" in r.output
    j = json.loads(run(cli.app, ["diff", "lab", "prod-a", "-o", "json"]).output)
    assert j["changes"][0]["key"] == "PriorityWeightQOS" and j["restart_keys"] == []
    assert run(cli.app, ["diff", "lab", "nope"]).exit_code == 1
    assert run(cli.app, ["apply", "lab", "prod-a", "--side", "both"]).exit_code == 1
    assert run(cli.app, ["apply", "lab", "Bad_Name"]).exit_code == 1
    assert run(cli.app, ["apply", "lab"]).exit_code == 2 and run(cli.app, ["apply", "nope", "x"]).exit_code == 2 and run(cli.app, ["apply"]).exit_code == 2
    monkeypatch.setattr(cli.checks, "GOV_SOCKET", str(tmp_path / "none.sock"))
    r = run(cli.app, ["apply", "lab", "prod-a"])
    assert r.exit_code == 1 and "governor is not reachable" in r.output
    r = run(cli.app, ["remove", "lab", "prod-a"])
    assert r.exit_code == 1 and "governor is not reachable" in r.output
    monkeypatch.setattr(cli, "_lab_env", lambda: env)
    assert run(cli.app, ["apply", "lab", "prod-a", "--execute"]).exit_code == 0
    assert run(cli.app, ["remove", "lab", "prod-a", "--execute"]).exit_code == 0


def _gov(tmp_path, lab_exists=None, lab_status=None, effector=None):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"mem_available": 4 * GB, "psi_some": 0, "psi_full": 0}))
    g = Governor(FileSampler(f), Ladder(), [], lambda a: None, False, effector=effector, lab_exists=lab_exists, lab_status=lab_status)
    g.step(0)
    return g, f


def d(g, req):
    return control.dispatch(json.dumps(req).encode(), g.handle, g.group_names)


def test_governor_rejects_bad_lab_commands_and_names(tmp_path):
    g, _ = _gov(tmp_path, lab_exists=lambda n: n == "prod-a")
    for bad in ({"cmd": "lab-apply", "name": "../x", "side": "baseline"}, {"cmd": "lab-apply", "name": "UP", "side": "baseline"}, {"cmd": "lab-apply", "name": "a" * 42, "side": "baseline"},
                {"cmd": "lab-apply", "name": "prod-a", "side": "both"}, {"cmd": "lab-restore", "name": 5}, {"cmd": "lab-restore"}):
        r = d(g, bad)
        assert r["ok"] is False and r["error"].startswith(bad["cmd"] + " needs name"), bad
    assert d(g, {"cmd": "lab-run", "name": "prod-a"})["error"].startswith("no lab runs without --workdir")  # issue 554 made the verb
    assert "not on disk" in d(g, {"cmd": "lab-restore", "name": "gone"})["error"] or "no lab gone on disk" in d(g, {"cmd": "lab-restore", "name": "gone"})["error"]


def test_governor_runs_lab_apply_and_restore_as_benchctl_changes(tmp_path):
    seen, done = [], threading.Event()

    def effector(a):
        seen.append(a)
        done.set()
        return 0

    g, _ = _gov(tmp_path, lab_exists=lambda n: True, lab_status=lambda: {"name": "prod-a", "side": "candidate", "time_scale": 1.0, "state": "applied"}, effector=effector)
    r = d(g, {"cmd": "lab-apply", "name": "prod-a", "side": "candidate"})
    assert r["ok"] and r["started"] and done.wait(5)
    assert seen == [["apply", "lab", "prod-a", "--side", "candidate", "--execute"]]
    for _ in range(100):
        if g.change["state"] != "running":
            break
        threading.Event().wait(0.05)
    done.clear()
    assert d(g, {"cmd": "lab-restore", "name": "prod-a"})["ok"] and done.wait(5)
    assert seen[1] == ["remove", "lab", "prod-a", "--execute"]
    st = g.handle("status", {})
    assert st["lab"] == {"name": "prod-a", "side": "candidate", "time_scale": 1.0, "state": "applied"}
    assert any("lab-apply=prod-a:candidate" in line for line in g.audit)


def test_governor_refuses_lab_changes_at_l2_with_one_message_and_without_workdir(tmp_path):
    g, f = _gov(tmp_path, lab_exists=lambda n: True)
    f.write_text(json.dumps({"mem_available": int(0.3 * GB), "psi_some": 0, "psi_full": 0}))
    for t in range(1, 40):
        g.step(float(t * 10))
        if g.ladder.level >= 2:
            break
    assert g.ladder.level >= 2
    r = d(g, {"cmd": "lab-apply", "name": "prod-a", "side": "baseline"})
    assert r["ok"] is False and r["error"].startswith(f"refused at L{g.ladder.level}: stack changes are paused")
    plain, _ = _gov(tmp_path)
    assert "no lab prod-a on disk" in d(plain, {"cmd": "lab-apply", "name": "prod-a", "side": "baseline"})["error"]
    assert plain.handle("status", {})["lab"] is None
