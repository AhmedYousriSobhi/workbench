"""Config lab users on the live stack (issue 869): `apply lab` loads the rewritten dump, creates the `lab-uNNN` Linux users (uid 30000+N) on
slurmctld, slurmdbd and the login node, and a job as `lab-u001` is accepted with associations enforced. Also records whether `sacctmgr load`
only adds or reconciles (the Slurm docs say "reconciled"; the answer goes in ADR 0050). Own cold start (ISOLATED_MODULES): accounting on, nothing
else optional; it removes the lab and stops the stack at the end.
Not run by the agent: `./scripts/verify.sh --unit lab_users --clean`."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from benchctl import lab, lab_apply, lab_users, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
NAME = "liveusers"


def ex(svc: str, *cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", f"workbench-{svc}-1", *cmd], capture_output=True, text=True, timeout=timeout, check=False)


@pytest.fixture(scope="module")
def stack_up():
    os.chdir(ROOT)
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=["accounting"], cpu_nodes=2, gpu_nodes=0))
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        raise AssertionError(("ENVIRONMENT, not a code defect: " if "REFUSED by governor" in why else "") + f"stack.up failed: {why}")
    assert {"slurmctld", "slurmdbd"} <= workbench.running_services(), workbench.running_services()
    yield sim, prof
    if lab_apply.read_marker():
        lab_apply.remove(NAME, lab_apply.Env(sim=sim, running=lambda s: s in workbench.running_services()))
    stack.down(say=lambda m: None)
    workbench.forget()


def test_lab_users_exist_and_may_submit(stack_up, tmp_path):
    sim, _ = stack_up
    export = tmp_path / "export"
    shutil.copytree(FIX, export)
    lab.import_lab(NAME, export, replace=True)
    env = lab_apply.Env(sim=sim, running=lambda s: s in workbench.running_services(), say=lambda m: None)
    before = ex("slurmctld", "sacctmgr", "-n", "-P", "show", "user", "format=User").stdout.split()
    lab_apply.apply(NAME, "baseline", env)
    mapping = lab_users.load(NAME)
    assert mapping, "no users.json"
    for svc in lab_users.SERVICES:
        for lab_user in mapping.values():
            r = ex(svc, "id", "-u", lab_user)
            assert r.returncode == 0 and r.stdout.strip() == str(lab_users.uid(lab_user)), (svc, lab_user, r.stderr)
    after = ex("slurmctld", "sacctmgr", "-n", "-P", "show", "user", "format=User").stdout.split()
    assert not [u for u in after if u in mapping], "a production name reached the accounting database"  # the keys of users.json are production names
    assert "lab-u001" in after or any(u.startswith("lab-u") for u in after), after
    # the question for ADR 0050: did `sacctmgr load` keep the users that were there before (add only) or drop them (reconcile)?
    print("sacctmgr load kept the earlier users:", all(u in after for u in before), before, after)
    user = min(mapping.values())
    r = ex("slurmctld", "runuser", "-u", user, "--", "sbatch", "--wrap", "hostname")
    assert r.returncode == 0, r.stderr
    assert ex("slurmctld", "sacct", "-a", "-n", "-X", "-o", "User").returncode == 0
    lab_apply.remove(NAME, env)
    for svc in lab_users.SERVICES:
        assert ex(svc, "id", user).returncode != 0, f"{user} is still on {svc}"
    assert not [u for u in ex("slurmctld", "sacctmgr", "-n", "-P", "show", "user", "format=User").stdout.split() if u.startswith("lab-u")]
