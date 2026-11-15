"""Kubernetes through Teleport (`tsh kube`, issue 383, ADR 0083): Teleport's Kubernetes Service registers the simulated cluster, a user whose
role grants the `workbench-k8s-viewers` group lists it, logs in and reads the KWOK nodes through the proxy, a write is refused by Kubernetes
RBAC, and a user whose role grants no Kubernetes group is refused. Needs `tsh` (same major as the server) and `kubectl` on the host, and
Docker; uses its own TELEPORT_HOME and KUBECONFIG so a developer's ~/.tsh and ~/.kube are never touched.

Written without a live run in the authoring session: if `tsh kube login` with an identity file (`-i`) does not give kubectl a working
credential, the helper `kubectl` below is the one place to change (see docs/guides/teleport-kube.md for what is proven)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import k8s, stack, teleport, workbench

ROOT = Path(__file__).resolve().parents[2]
PROXY = teleport.proxy_addr(yaml.safe_load((ROOT / "sim.yaml").read_text()))
CLUSTER = k8s.CONTEXT
OUTSIDER = "nokube"  # a Teleport user whose role has no kubernetes_groups, made by this module and removed at the end


def tsh(home: Path, *args: str, user: str = "alice", extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "TELEPORT_HOME": str(home), **(extra_env or {})}
    identity = ROOT / teleport.SECRETS_DIR / "identity" / user
    return subprocess.run(["tsh", f"--proxy={PROXY}", "--insecure", "-i", str(identity), *args],
                          capture_output=True, text=True, timeout=60, check=False, env=env)


def kubectl(home: Path, user: str, *args: str) -> subprocess.CompletedProcess:
    """kubectl through the Teleport proxy after `tsh kube login`: the kubeconfig is private to this test."""
    kc = home / f"kubeconfig-{user}"
    login = tsh(home, "kube", "login", CLUSTER, user=user, extra_env={"KUBECONFIG": str(kc)})
    if login.returncode != 0:
        return login
    env = {**os.environ, "TELEPORT_HOME": str(home), "KUBECONFIG": str(kc)}
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=60, check=False, env=env)


def tctl(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-i", "workbench-teleport-1", "tctl", *args], input=stdin,
                          capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture(scope="module")
def tele(tmp_path_factory):
    for tool in ("tsh", "kubectl"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} is not installed on this host")
    os.chdir(ROOT)
    sim, prof = workbench.effective(yaml.safe_load((ROOT / "sim.yaml").read_text()),
                                    workbench.Spec(parts=["teleport", "k8s"], cpu_nodes=1, gpu_nodes=0, login_nodes=["both"]))
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        if "REFUSED by governor" in why:
            raise AssertionError(f"ENVIRONMENT, not a code defect: the governor refused `up` (free host memory): {why}")
        raise AssertionError(f"stack.up failed: {why}")
    home = tmp_path_factory.mktemp("tsh")
    assert wait_for(lambda: tsh(home, "ls").returncode == 0, timeout=60), tsh(home, "ls").stderr
    role = yaml.safe_dump({"kind": "role", "version": "v7", "metadata": {"name": f"{OUTSIDER}-role"},
                           "spec": {"allow": {"kubernetes_labels": {"*": "*"}, "logins": ["alice"]}}})
    user = yaml.safe_dump({"kind": "user", "version": "v2", "metadata": {"name": OUTSIDER}, "spec": {"roles": [f"{OUTSIDER}-role"]}})
    assert tctl("create", "-f", stdin=role + "---\n" + user).returncode == 0
    r = tctl("auth", "sign", f"--user={OUTSIDER}", "--ttl=1h", "--format=file", f"--out={teleport.CONTAINER_SECRETS}/identity/{OUTSIDER}", "--overwrite")
    assert r.returncode == 0, r.stderr
    yield home
    tctl("rm", f"user/{OUTSIDER}")
    tctl("rm", f"role/{OUTSIDER}-role")
    (ROOT / teleport.SECRETS_DIR / "identity" / OUTSIDER).unlink(missing_ok=True)
    stack.down(say=lambda m: None)


def test_the_kubernetes_service_registers_the_cluster_on_the_same_port(tele):
    assert wait_for(lambda: CLUSTER in {c["kube_cluster_name"] for c in json.loads(tsh(tele, "kube", "ls", "--format=json").stdout or "[]")}, timeout=60), \
        tsh(tele, "kube", "ls").stderr
    ports = subprocess.run(["docker", "port", "workbench-teleport-1"], capture_output=True, text=True, timeout=30, check=False).stdout
    assert sorted({line.split("/")[0] for line in ports.splitlines()}) == [str(teleport.DIAG_PORT), str(teleport.PROXY_PORT)]  # no new port


def test_a_viewer_reads_the_kwok_nodes_through_teleport(tele):
    r = kubectl(tele, "alice", "get", "nodes", "-o", "name")
    assert r.returncode == 0 and "node/kcn001" in r.stdout, r.stderr


def test_a_write_is_refused_by_kubernetes_rbac_for_that_user(tele):
    r = kubectl(tele, "alice", "create", "namespace", "teleport-must-not-create-this")
    assert r.returncode != 0 and "forbidden" in r.stderr.lower(), r.stderr
    can = kubectl(tele, "alice", "auth", "can-i", "delete", "nodes")
    assert can.stdout.strip() == "no", can.stderr


def test_a_user_without_a_kubernetes_group_is_denied(tele):
    r = kubectl(tele, OUTSIDER, "get", "nodes", "-o", "name")
    assert r.returncode != 0 and "node/" not in r.stdout, r.stdout
    assert "forbidden" in r.stderr.lower() or "access denied" in r.stderr.lower() or "not found" in r.stderr.lower(), r.stderr


def test_the_teleport_container_stays_within_its_memory_limit(tele):
    info = json.loads(subprocess.run(["docker", "inspect", "workbench-teleport-1"], capture_output=True, text=True, timeout=30, check=False).stdout)[0]
    assert not info["HostConfig"]["CapAdd"] and not info["HostConfig"]["Privileged"]
    stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "workbench-teleport-1"],
                           capture_output=True, text=True, timeout=30, check=False).stdout
    print("teleport memory with the Kubernetes Service:", stats.strip())  # recorded in ADR 0083
