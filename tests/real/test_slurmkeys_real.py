"""The munge and JWT keys come from `up`, not from the image (issue 465, SEC1). Shared stack."""
import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, release

ROOT = Path(__file__).resolve().parents[2]
KEYS = ROOT / "compose/generated/secrets/slurm"
IMAGES = ("workbench/node-base:dev", "workbench/slurm-ctl:dev", "workbench/login:dev")


def sh(*cmd: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False, input=stdin)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def stack_up():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    yield
    release()


def test_no_image_layer_holds_a_key(stack_up):  # stack_up builds the images first: an older build still holds a key
    for image in IMAGES:
        r = sh("docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
               "test ! -e /etc/munge/munge.key && test ! -e /var/spool/slurmctld/jwt_hs256.key")
        assert r.returncode == 0, f"{image} holds a key: {r.stderr}"


def test_a_container_without_the_mount_refuses_with_one_message(stack_up):
    r = sh("docker", "run", "--rm", IMAGES[0], "true")
    assert r.returncode == 1 and "benchctl up" in r.stderr and r.stderr.count("\n") == 1, r.stderr


def test_every_slurm_container_has_exactly_the_hosts_keys(stack_up):
    munge, jwt = sha(KEYS / "munge.key"), sha(KEYS / "jwt_hs256.key")
    for c in ("workbench-slurmctld-1", "workbench-cn001-1", "workbench-login-1", "workbench-slurmrestd-1"):
        m = sh("docker", "exec", c, "sha256sum", "/etc/munge/munge.key").stdout.split()[0]
        j = sh("docker", "exec", c, "sha256sum", "/var/spool/slurmctld/jwt_hs256.key").stdout.split()[0]
        assert (m, j) == (munge, jwt), c
    owner = sh("docker", "exec", "workbench-cn001-1", "stat", "-c", "%U:%G %a", "/etc/munge/munge.key").stdout.strip()
    assert owner == "munge:munge 400"


def test_munge_and_the_jwt_path_work_with_the_generated_keys(stack_up):
    cred = sh("docker", "exec", "workbench-cn001-1", "munge", "-n").stdout
    assert sh("docker", "exec", "-i", "workbench-slurmctld-1", "unmunge", stdin=cred).returncode == 0
    tok = sh("docker", "exec", "workbench-slurmctld-1", "su", "-s", "/bin/sh", "alice", "-c", "scontrol token username=alice").stdout
    assert "SLURM_JWT=" in tok  # slurmctld signs with the mounted key, ADR 0003
