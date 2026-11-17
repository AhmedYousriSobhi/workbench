"""The lab's stable lab-uNNN mapping, the rewritten dump and the Linux users (issue 869). No Docker."""
import json
import re
import stat
from pathlib import Path

import pytest
import yaml

from benchctl import lab, lab_apply, lab_run, lab_users
from benchctl.lab import LabError

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())

DUMP = ("Cluster - 'prod'\nAccount - 'ml':Description='ml of alice':Organization='sci':Coordinators='alice,bob'\nParent - 'ml'\n"
        "User - 'alice':DefaultAccount='ml'\nUser - 'bob':DefaultAccount='ml'\nUser - 'root':DefaultAccount='root'\n")


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "lab"
    lab.import_lab("prod-a", FIX, root=r)
    return r


def test_two_seeds_give_the_same_name_to_the_same_user(root):
    a = {j["user"] for j in lab_run.build_jobs("prod-a", SIM, root, seed=1)[0]["jobs"]}
    first = lab_users.load("prod-a", root)
    b = {j["user"] for j in lab_run.build_jobs("prod-a", SIM, root, seed=2, target_hours=3)[0]["jobs"]}
    assert lab_users.load("prod-a", root) == first
    assert a and b <= set(first.values())


def test_mapping_is_numbered_once_then_only_grows(root):
    m = lab_users.ensure("prod-a", {"carol", "alice"}, root)
    assert (m["alice"], m["carol"]) == ("lab-u001", "lab-u002")
    m2 = lab_users.ensure("prod-a", {"aaron", "alice"}, root)
    assert m2["alice"] == "lab-u001" and m2["aaron"] == "lab-u003"  # a new name goes at the end, no number is reused


def test_mapping_file_is_private(root):
    lab_users.ensure("prod-a", {"x"}, root)
    p = lab_users.users_path("prod-a", root)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_preview_writes_nothing(root):
    lab_users.for_lab("prod-a", root, write=False)
    assert not lab_users.users_path("prod-a", root).exists()


def test_dump_is_rewritten_and_root_stays():
    m = {"alice": "lab-u001", "bob": "lab-u002"}
    out = lab_users.rewrite_dump(DUMP, m)
    assert "alice" not in out and "bob" not in out
    assert "User - 'lab-u001'" in out and "Coordinators='lab-u001,lab-u002'" in out and "Description='ml of lab-u001'" in out
    assert "User - 'root'" in out


def test_dump_rewrite_leaves_names_that_only_contain_a_user():
    out = lab_users.rewrite_dump("Account - 'a':Description='alicia and alice.b'\n", {"alice": "lab-u001"})
    assert "alicia and alice.b" in out


def test_uid_rule_matches_the_driver():
    src = (ROOT / "images" / "slurm-ctl" / "lab_driver.py").read_text()
    assert int(re.search(r"^UID_BASE = (\d+)", src, re.MULTILINE).group(1)) == lab_users.UID_BASE
    assert lab_users.uid("lab-u001") == 30001 and lab_users.UID_BASE > 1001
    with pytest.raises(lab_users.UsersError):
        lab_users.uid("alice")


def test_scripts_create_and_remove_every_user():
    m = {"a": "lab-u001", "b": "lab-u002"}
    assert "useradd -M -u 30002 -s /bin/sh lab-u002" in lab_users.setup_script(m)
    assert "userdel lab-u001" in lab_users.teardown_script(m)


def test_import_replace_keeps_the_numbers(root):
    first = lab_users.for_lab("prod-a", root)
    lab.import_lab("prod-a", FIX, root=root, replace=True)
    assert lab_users.load("prod-a", root) == first


def test_import_replace_refused_while_a_run_is_active(root):
    (root / "run-active.json").write_text(json.dumps({"name": "prod-a", "run": "20260101-000000"}))
    with pytest.raises(LabError, match="active or recovering"):
        lab.import_lab("prod-a", FIX, root=root, replace=True)


def test_apply_creates_users_and_loads_the_rewritten_dump(root):
    calls = []

    class R:
        returncode, stdout, stderr = 0, "", ""

    env = lab_apply.Env(root=root)
    env.running = lambda s: True
    env.exec = lambda svc, *cmd, input=None: (calls.append((svc, cmd, input)), R())[1]
    rd = root / "prod-a" / "render"
    rd.mkdir(parents=True, exist_ok=True)
    env.runner = lambda *a, **k: R()
    lab_apply._apply_sacct("prod-a", {"sacctmgr.dump": DUMP.replace("alice", "u1").replace("bob", "u2")}, env, rd)
    svcs = [c[0] for c in calls if "useradd" in c[1][-1]]
    assert svcs == ["slurmctld", "slurmdbd", "login"]
    loaded = [c[2] for c in calls if c[2]][-1]
    assert "u1" not in loaded.replace("lab-u1", "") and "User - 'lab-u" in loaded
