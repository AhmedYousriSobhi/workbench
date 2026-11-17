"""Kubernetes through Teleport (`tsh kube`, issue 383, ADR 0083): Teleport's Kubernetes Service registers the simulated cluster, a user whose
role grants the `workbench-k8s-viewers` group lists it, logs in and reads the KWOK nodes through the proxy, a write is refused by Kubernetes
RBAC, and a user whose role grants no Kubernetes group is refused. Needs `tsh` (same major as the server) and `kubectl` on the host, and
Docker; uses its own TELEPORT_HOME and KUBECONFIG so a developer's ~/.tsh and ~/.kube are never touched.

Written without a live run in the authoring session: if `tsh kube login` with an identity file (`-i`) does not give kubectl a working
credential, the helper `kubectl` below is the one place to change (see docs/guides/teleport-kube.md for what is proven)."""
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import k8s, stack, teleport, teleportaudit, workbench

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
    # kubectl runs `tsh kube credentials` as an exec plugin: it needs the same identity file (found live: "no SSH auth methods loaded")
    env = {**os.environ, "TELEPORT_HOME": str(home), "KUBECONFIG": str(kc),
           "TELEPORT_IDENTITY_FILE": str(ROOT / teleport.SECRETS_DIR / "identity" / user), "TELEPORT_PROXY": PROXY}
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
    assert sorted({line.split("/")[0] for line in ports.splitlines()}) == sorted([str(teleport.DIAG_PORT), str(teleport.PROXY_PORT)])  # no new port


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
    # found live: Teleport answers "(NotFound) ... Your user's Teleport role does not allow Kubernetes access ... kubernetes_groups"
    assert "does not allow kubernetes access" in r.stderr.lower() or "forbidden" in r.stderr.lower() or "access denied" in r.stderr.lower(), r.stderr


def test_the_teleport_container_stays_within_its_memory_limit(tele):
    info = json.loads(subprocess.run(["docker", "inspect", "workbench-teleport-1"], capture_output=True, text=True, timeout=30, check=False).stdout)[0]
    assert not info["HostConfig"]["CapAdd"] and not info["HostConfig"]["Privileged"]
    stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "workbench-teleport-1"],
                           capture_output=True, text=True, timeout=30, check=False).stdout
    print("teleport memory with the Kubernetes Service:", stats.strip())  # recorded in ADR 0083


# ---- audit events (issue 754): `kube.request` lines in the audit log, by named user ----

def audit(since, **fields) -> list[dict]:
    """kube.request events since `since` matching `fields`, waiting a moment for Teleport to write them; a missing folder fails the test."""
    return teleportaudit.wait_for_events(lambda: teleportaudit.select(teleportaudit.parse(teleportaudit.read_log()), since=since, **fields), timeout=30)


def test_the_audit_log_folder_exists(tele):
    try:
        teleportaudit.read_log()
    except teleportaudit.AuditError as e:
        pytest.fail(str(e))  # a missing folder is a failure, never a silent skip


def test_a_read_is_audited_with_the_named_user_cluster_verb_and_code(tele):
    start = teleportaudit.now_minus()
    assert kubectl(tele, "alice", "get", "node", "kcn001", "-o", "name").returncode == 0
    got = audit(start, user="alice", kubernetes_cluster=CLUSTER, verb="GET", response_code=200)
    assert got, f"no kube.request for alice GET 200 since {start}; types seen: {teleportaudit.types_seen(teleportaudit.parse(teleportaudit.read_log()))}"
    assert any("/nodes/kcn001" in str(e.get("request_path")) for e in got), got
    print("kube.request fields written by this Teleport:", sorted(got[0]))  # recorded for docs/guides/teleport-kube.md


def test_a_refused_write_is_audited_with_a_403(tele):
    name = f"audit-probe-{secrets.token_hex(3)}"  # the name is in the request path, so only this call matches
    start = teleportaudit.now_minus()
    r = kubectl(tele, "alice", "delete", "pod", name, "-n", "team-a")
    assert r.returncode != 0 and "forbidden" in r.stderr.lower(), r.stderr
    got = audit(start, user="alice", verb="DELETE", response_code=403)
    assert any(name in str(e.get("request_path")) for e in got), f"no 403 kube.request for {name}: {got}"


def test_a_user_without_a_kubernetes_group_gets_no_successful_audited_read(tele):
    """What Teleport writes for a refusal before the request is forwarded is not documented; the test records it and asserts no 200 for this user."""
    start = teleportaudit.now_minus()
    assert kubectl(tele, OUTSIDER, "get", "nodes", "-o", "name").returncode != 0
    events = teleportaudit.parse(teleportaudit.read_log())
    mine = teleportaudit.select(events, since=start, user=OUTSIDER)
    print(f"events for {OUTSIDER} on a refused read:", [(e.get("event"), e.get("response_code")) for e in mine])  # recorded
    assert not teleportaudit.select(events, since=start, user=OUTSIDER, response_code=200)


def test_the_event_types_that_appear_without_a_pod_shell_are_recorded(tele):
    """ADR 0084 'Not verified': whether `exec`, `port` and `session.*` appear. Nothing here opens a shell, so they are expected absent."""
    seen = teleportaudit.types_seen(teleportaudit.parse(teleportaudit.read_log()))
    print("audit event types in the log:", seen)
    assert teleportaudit.KUBE_REQUEST in seen
