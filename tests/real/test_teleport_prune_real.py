"""Teleport forgets a node the selection drops (issues 205, 212): build with login nodes `ssh,teleport`, rebuild with `ssh` only,
and `tctl get nodes` no longer lists login2 while its host certificate is gone from the secrets folder. The ssh-only login
stays out of Teleport throughout. Own stack (ISOLATED_MODULES): Teleport and a custom login selection."""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from benchctl import stack, teleport, workbench

ROOT = Path(__file__).resolve().parents[2]
HOSTS = ROOT / teleport.SECRETS_DIR / "hosts"


def up(logins: list[str]) -> None:
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=["teleport"], cpu_nodes=1, gpu_nodes=0, login_nodes=logins, controller=False))
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        if "REFUSED by governor" in why:
            raise AssertionError(f"ENVIRONMENT, not a code defect: the governor refused `up` (free host memory): {why}")
        raise AssertionError(f"stack.up failed: {why}")


def registered() -> set[str]:
    """Host names `tctl get nodes` lists, read inside the Teleport container."""
    r = teleport._tctl(stack._run, stack.dc, "get", "nodes", "--format=json")
    assert r.returncode == 0, r.stderr
    return {n["spec"]["hostname"] for n in json.loads(r.stdout or "[]")}


@pytest.fixture(scope="module")
def tele():
    os.chdir(ROOT)
    up(["ssh", "teleport"])  # login (ssh only), login2 (teleport)
    yield
    stack.down(say=lambda m: None)
    workbench.forget()  # `up` saves the selection; leaving it would change every later test's node list


def test_the_teleport_login_is_registered_and_has_a_host_certificate(tele):
    assert {"login2", "cn001"} <= registered() and "login" not in registered()
    assert (HOSTS / "login2-cert.pub").is_file()


def test_dropping_the_login_forgets_its_registration_and_certificate(tele):
    up(["ssh"])
    names = registered()
    assert "login2" not in names, names
    assert "cn001" in names, names  # what the selection still has is kept
    assert not (HOSTS / "login2-cert.pub").exists() and not (HOSTS / "login2").exists()
    assert (HOSTS / "cn001-cert.pub").is_file()
    running = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.project=workbench", "--format",
                              '{{.Label "com.docker.compose.service"}}'], capture_output=True, text=True, check=False).stdout.split()
    assert "login2" not in running, running  # and the container is gone too
