"""Teleport access (ADR 0020, issue 39): the proxy answers, the nodes are registered, and `tsh ssh` reaches the login
node and a compute node with a certificate from the user's identity file. Needs `tsh` on the host (same major as the
server, sim.yaml teleport.version) and Docker; uses its own TELEPORT_HOME so a developer's ~/.tsh is never touched."""
import json
import os
import shutil
import ssl
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import stack, teleport, workbench

ROOT = Path(__file__).resolve().parents[2]
PROXY = f"127.0.0.1:{teleport.PROXY_PORT}"


def tsh(home: Path, *args: str, user: str = "alice") -> subprocess.CompletedProcess:
    env = {**os.environ, "TELEPORT_HOME": str(home)}
    identity = ROOT / teleport.SECRETS_DIR / "identity" / user
    return subprocess.run(["tsh", f"--proxy={PROXY}", "--insecure", "-i", str(identity), *args],
                          capture_output=True, text=True, timeout=60, check=False, env=env)


@pytest.fixture(scope="module")
def tele(tmp_path_factory):
    if not shutil.which("tsh"):
        pytest.skip("tsh is not installed on this host")
    os.chdir(ROOT)
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=["teleport"], cpu_nodes=2, gpu_nodes=0))
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        if "REFUSED by governor" in why:
            raise AssertionError(f"ENVIRONMENT, not a code defect: the governor refused `up` (free host memory): {why}")
        raise AssertionError(f"stack.up failed: {why}")
    home = tmp_path_factory.mktemp("tsh")
    assert wait_for(lambda: tsh(home, "ls").returncode == 0, timeout=60), tsh(home, "ls").stderr
    yield home
    stack.down(say=lambda m: None)


def test_the_proxy_answers_with_the_cluster_name(tele):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    ping = json.loads(urllib.request.urlopen(f"https://{PROXY}/webapi/ping", context=ctx, timeout=10).read())
    assert ping["cluster_name"] == teleport.CLUSTER and ping["server_version"].count(".") == 2


def test_every_node_and_the_login_node_are_registered(tele):
    r = tsh(tele, "ls", "--format=json")
    names = {n["spec"]["hostname"] for n in json.loads(r.stdout)}
    assert {"login", "cn001", "cn002"} <= names, r.stderr


def test_tsh_ssh_reaches_the_login_node_and_a_compute_node(tele):
    for node in ("login", "cn001"):
        r = tsh(tele, "ssh", f"alice@{node}", "hostname; id -un")
        assert r.returncode == 0 and r.stdout.split() == [node, "alice"], r.stderr


def test_a_login_the_role_does_not_allow_is_refused(tele):
    assert tsh(tele, "ssh", "root@cn001", "true").returncode != 0
    assert tsh(tele, "ssh", "mallory@cn001", "true").returncode != 0


def test_an_invite_link_can_be_made_for_a_user(tele):
    url = teleport.invite("alice", stack._run, stack.dc)
    assert url.startswith(f"https://{PROXY}/web/")
