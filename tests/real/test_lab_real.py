"""Config lab validation against the real tools (issue 552, SPEC C-24). WRITTEN, NOT RUN by the agent: `./scripts/verify.sh --unit lab --clean`.

Own cold start with accounting, Kubernetes and Teleport (ISOLATED_MODULES). For each tool the same pair: green on the fixture set
(tests/fixtures/lab/full), red on the deliberately broken set (tests/fixtures/lab/broken). Asserts on what the real tools say and on what
is left behind (no one-shot container, no scratch database, no temporary Teleport role)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from benchctl import lab, lab_validate, workbench
from benchctl import stack as stackmod

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lab"
GOOD, BAD = "lab-real-good", "lab-real-bad"


def sh(*cmd: str, stdin: str | None = None) -> str:
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=60, check=False, input=stdin)
    return r.stdout + r.stderr


@pytest.fixture(scope="module")
def lab_cluster():
    os.chdir(ROOT)
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=["accounting", "k8s", "teleport"], cpu_nodes=2, gpu_nodes=0))
    said: list[str] = []
    if not stackmod.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        raise AssertionError(("ENVIRONMENT, not a code defect: " if "REFUSED by governor" in why else "") + f"stack.up failed: {why}")
    for name, which in ((GOOD, "full"), (BAD, "broken")):
        lab.import_lab(name, FIX / which, replace=True, sim=sim)
    yield sim
    for name in (GOOD, BAD):
        shutil.rmtree(lab.path(name), ignore_errors=True)
    stackmod.down(say=lambda m: None)
    workbench.forget()


def results(sim, name):
    return {r["tool"]: r for r in lab_validate.run(name, sim)["results"]}


def test_slurm_green_and_red(lab_cluster):
    assert results(lab_cluster, GOOD)["slurm.conf"]["status"] == "passed"
    red = results(lab_cluster, BAD)["slurm.conf"]
    assert red["status"] == "failed" and "fatal" in red["messages"][0]


def test_sacctmgr_load_green_and_nothing_left_behind(lab_cluster):
    r = results(lab_cluster, GOOD)["sacctmgr load"]
    assert r["status"] == "passed", r
    dbs = sh("docker", "exec", "-i", "workbench-mariadb-1", "sh", "-c", 'exec mariadb -uslurm -p"$MARIADB_PASSWORD" -N', stdin="SHOW DATABASES LIKE 'lab\\_validate\\_%';")
    assert "lab_validate_" not in dbs
    assert "lab-validate-" not in sh("docker", "ps", "-a", "--format", "{{.Names}}")


def test_sacctmgr_load_red_on_a_dump_the_real_tool_refuses(lab_cluster):
    d = lab.path(GOOD) / "baseline" / "sacctmgr.dump"
    good = d.read_text()
    try:
        d.write_text(good + "Account - 'bad':Description='x':Organization='y':Fairshare=notanumber\n")
        assert results(lab_cluster, GOOD)["sacctmgr load"]["status"] == "failed"
    finally:
        d.write_text(good)


def test_promtool_green_and_red(lab_cluster):
    good = results(lab_cluster, GOOD)
    assert good["promtool check rules"]["status"] == good["promtool test rules"]["status"] == "passed"
    bad = results(lab_cluster, BAD)
    assert bad["promtool check rules"]["status"] == "failed" and "parenthesis" in " ".join(bad["promtool check rules"]["messages"])


def test_kubectl_dry_run_green_and_red(lab_cluster):
    assert results(lab_cluster, GOOD)["kubectl dry-run"]["status"] == "passed"
    p = lab.path(GOOD) / "baseline" / "k8s" / "policy.yaml"
    good = p.read_text()
    try:
        p.write_text(good + "---\napiVersion: v1\nkind: ResourceQuota\nmetadata: {name: broken, namespace: team-a}\nspec: {hard: {requests.cpu: not-a-quantity}}\n")
        assert results(lab_cluster, GOOD)["kubectl dry-run"]["status"] == "failed"
    finally:
        p.write_text(good)


def test_teleport_role_is_accepted_by_teleport_and_removed(lab_cluster):
    r = results(lab_cluster, GOOD)["teleport role"]
    assert r["status"] == "passed" and r["note"] == "accepted by Teleport", r
    assert "lab-validate-" not in sh("docker", "exec", "workbench-teleport-1", "tctl", "get", "roles", "--format=text")
    bad = results(lab_cluster, BAD)["teleport role"]
    assert bad["status"] == "failed" and any("did you mean" in m for m in bad["messages"])


def test_pause_flag_and_class_label_are_absent_from_every_one_shot(lab_cluster):
    # one-shots never carry the governed label: while a run is going nothing governed may appear beyond the stack's own containers
    assert "lab-validate-" not in sh("docker", "ps", "-a", "--filter", "label=workbench.class", "--format", "{{.Names}}")
