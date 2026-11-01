"""Teleport access (ADR 0020, issue 39) and login nodes (ADR 0023, issue 189): the proxy answers, the nodes are registered,
and `tsh ssh` reaches the login nodes that use Teleport and a compute node with a certificate from the user's identity
file; a `teleport` login takes no key, an `ssh` login is not registered, a `both` login does either. Needs `tsh` on the host (same major as the
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
                                    workbench.Spec(parts=["teleport"], cpu_nodes=2, gpu_nodes=0,
                                                    login_nodes=["both", "teleport", "ssh"]))  # login, login2, login3
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


def test_every_node_and_the_teleport_logins_are_registered(tele):
    r = tsh(tele, "ls", "--format=json")
    names = {n["spec"]["hostname"] for n in json.loads(r.stdout)}
    assert {"login", "login2", "cn001", "cn002"} <= names and "login3" not in names, r.stderr  # login3 is direct SSH only


def test_tsh_ssh_reaches_the_teleport_logins_and_a_compute_node(tele):
    for node in ("login", "login2", "cn001"):
        r = tsh(tele, "ssh", f"alice@{node}", "hostname; id -un")
        assert r.returncode == 0 and r.stdout.split() == [node, "alice"], r.stderr


def test_a_login_the_role_does_not_allow_is_refused(tele):
    assert tsh(tele, "ssh", "root@cn001", "true").returncode != 0
    assert tsh(tele, "ssh", "mallory@cn001", "true").returncode != 0


def test_an_invite_link_can_be_made_for_a_user(tele):
    url = teleport.invite("alice", stack._run, stack.dc)
    assert url.startswith(f"https://{PROXY}/web/")


KEY = ROOT / "compose/generated/secrets/ssh/id_ed25519"


def key_login(host: str, port: int) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-i", str(KEY), "-p", str(port), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                           "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", f"alice@{host}", "hostname"],
                          capture_output=True, text=True, timeout=30, check=False)


def test_direct_key_login_works_where_the_mode_allows_it_and_is_refused_otherwise(tele):
    from benchctl import addressing
    sim_ips = addressing.plan({**yaml.safe_load((ROOT / "sim.yaml").read_text()), "login": ["both", "teleport", "ssh"]}, 2)["logins"]
    assert key_login("127.0.0.1", 2222).stdout.strip() == "login"     # both: the published port
    assert key_login("127.0.0.1", 2224).stdout.strip() == "login3"    # ssh: the third node's port
    refused = key_login(sim_ips["login2"], 22)                         # teleport only: reachable on the network, but no key is accepted
    assert refused.returncode != 0 and "Permission denied" in refused.stderr, refused.stderr
    assert key_login("127.0.0.1", 2223).returncode != 0                # and no port is published for it
