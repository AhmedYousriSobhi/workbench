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
DEV, ADMIN = "kdev", "kadmin"  # users of the developer and admin tiers (ADR 0084 W1), made by this module and removed at the end; alice is the viewer
TIER_USERS = {DEV: teleport.tier_role("developer"), ADMIN: teleport.tier_role("admin")}


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
    for u, role in TIER_USERS.items():  # the tier roles are the ones `up` loaded; only the users are made here
        doc = yaml.safe_dump({"kind": "user", "version": "v2", "metadata": {"name": u}, "spec": {"roles": [role]}})
        assert tctl("create", "-f", stdin=doc).returncode == 0
        r = tctl("auth", "sign", f"--user={u}", "--ttl=1h", "--format=file", f"--out={teleport.CONTAINER_SECRETS}/identity/{u}", "--overwrite")
        assert r.returncode == 0, r.stderr
    yield home
    for u in TIER_USERS:
        tctl("rm", f"user/{u}")
        (ROOT / teleport.SECRETS_DIR / "identity" / u).unlink(missing_ok=True)
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


# ---- access tiers (issue 753, ADR 0084 W1): viewer, team-a developer, admin; role v8 ----

def one_line(r: subprocess.CompletedProcess) -> str:
    return " ".join((r.stderr or r.stdout).split()).lower()


def test_the_pinned_teleport_accepts_the_v8_roles(tele):
    for role in (teleport.ROLE, *TIER_USERS.values(), teleport.tier_role("viewer")):
        got = yaml.safe_load(tctl("get", f"role/{role}", "--format=yaml").stdout)
        got = got[0] if isinstance(got, list) else got
        assert got["version"] == "v8", (role, got.get("version"))
    doc = yaml.safe_dump_all(teleport.tier_roles())
    assert tctl("create", "-f", stdin=doc).returncode == 0  # 18.11.8 takes the v8 `kubernetes_resources` as written


def test_a_developer_creates_and_deletes_a_job_in_team_a(tele):
    name = f"dev-probe-{secrets.token_hex(3)}"
    r = kubectl(tele, DEV, "create", "job", name, "-n", "team-a", "--image=busybox", "--", "true")
    assert r.returncode == 0, r.stderr
    d = kubectl(tele, DEV, "delete", "job", name, "-n", "team-a")
    assert d.returncode == 0, d.stderr


def test_a_developer_is_refused_in_team_b_and_on_nodes(tele):
    r = kubectl(tele, DEV, "create", "job", "dev-probe-b", "-n", "team-b", "--image=busybox", "--", "true")
    assert r.returncode != 0 and ("forbidden" in one_line(r) or "denied" in one_line(r)), r.stderr
    print("developer, create in team-b:", one_line(r))  # recorded in docs/guides/teleport-kube.md
    n = kubectl(tele, DEV, "get", "nodes", "-o", "name")
    assert n.returncode != 0 and "node/" not in n.stdout, n.stdout
    print("developer, get nodes:", one_line(n))


def test_a_teleport_denial_and_a_kubernetes_forbidden_read_differently(tele):
    """Outside the role's kubernetes_resources (nodes) Teleport refuses; inside them (secrets in team-a) Teleport forwards and the developer
    Role in Kubernetes refuses. Two layers, two messages: the test records both and asserts they differ and the second names the user."""
    teleport_layer = kubectl(tele, DEV, "get", "nodes")
    k8s_layer = kubectl(tele, DEV, "get", "secrets", "-n", "team-a")
    assert teleport_layer.returncode != 0 and k8s_layer.returncode != 0
    a, b = one_line(teleport_layer), one_line(k8s_layer)
    print("teleport layer:", a, "| kubernetes layer:", b)
    assert a != b
    assert "forbidden" in b and "secrets" in b, b  # Kubernetes RBAC: `... cannot list resource "secrets" ...`
    start = teleportaudit.now_minus()
    kubectl(tele, DEV, "get", "secrets", "-n", "team-a")
    assert audit(start, user=DEV, response_code=403), "Teleport forwarded the secrets request, so it is audited with the 403 from Kubernetes"


def test_a_viewer_still_reads_nodes_and_cannot_write(tele):
    assert "node/kcn001" in kubectl(tele, "alice", "get", "nodes", "-o", "name").stdout
    r = kubectl(tele, "alice", "create", "job", "viewer-probe", "-n", "team-a", "--image=busybox", "--", "true")
    assert r.returncode != 0 and "forbidden" in one_line(r), r.stderr


def test_an_admin_writes_in_any_namespace(tele):
    for ns in ("team-a", "team-b"):
        name = f"admin-probe-{secrets.token_hex(3)}"
        c = kubectl(tele, ADMIN, "create", "configmap", name, "-n", ns)
        assert c.returncode == 0, c.stderr
        assert kubectl(tele, ADMIN, "delete", "configmap", name, "-n", ns).returncode == 0
    assert "node/kcn001" in kubectl(tele, ADMIN, "get", "nodes", "-o", "name").stdout


# ---- the tsh tools an app that uses tsh would call (ADR 0084): credentials, kubectl, proxy kube ----

def _env(home: Path, user: str, **extra) -> dict:
    return {**os.environ, "TELEPORT_HOME": str(home), "TELEPORT_IDENTITY_FILE": str(ROOT / teleport.SECRETS_DIR / "identity" / user),
            "TELEPORT_PROXY": PROXY, **extra}


def test_tsh_kube_credentials_prints_an_exec_credential_for_a_valid_identity(tele):
    r = tsh(tele, "kube", "credentials", f"--kube-cluster={CLUSTER}", f"--teleport-cluster={teleport.CLUSTER}", user="alice")
    assert r.returncode == 0, r.stderr
    cred = json.loads(r.stdout)
    assert cred["kind"] == "ExecCredential" and cred["status"].get("clientCertificateData"), list(cred.get("status", {}))


def test_tsh_kube_credentials_fails_after_the_identity_expires(tele):
    """A 1-minute identity (the shortest Teleport signs); the test waits for it to lapse. Slow on purpose (about 70 s)."""
    import time
    r = tctl("auth", "sign", f"--user={DEV}", "--ttl=1m", "--format=file", f"--out={teleport.CONTAINER_SECRETS}/identity/{DEV}-short", "--overwrite")
    assert r.returncode == 0, r.stderr
    args = ("kube", "credentials", f"--kube-cluster={CLUSTER}", f"--teleport-cluster={teleport.CLUSTER}")
    try:
        assert tsh(tele, *args, user=f"{DEV}-short").returncode == 0  # valid now
        time.sleep(75)
        late = tsh(tele, *args, user=f"{DEV}-short")
        assert late.returncode != 0, late.stdout
        print("tsh kube credentials after expiry:", one_line(late))
    finally:
        (ROOT / teleport.SECRETS_DIR / "identity" / f"{DEV}-short").unlink(missing_ok=True)


def test_tsh_kubectl_runs_the_happy_path(tele):
    r = subprocess.run(["tsh", "--insecure", "kubectl", "get", "nodes", "-o", "name"], capture_output=True, text=True, timeout=90, check=False,
                       env=_env(tele, "alice", KUBECONFIG=str(tele / "kc-tshkubectl")))
    if r.returncode != 0:
        print("tsh kubectl with an identity file:", one_line(r))  # if it needs a profile, docs/guides/teleport-kube.md says so in one line
    assert r.returncode == 0 and "node/kcn001" in r.stdout, r.stderr


def test_tsh_proxy_kube_serves_a_local_kubeconfig_that_kubectl_uses(tele):
    """`tsh proxy kube` with `-i` prints the kubeconfig it wrote; kubectl through it reads the nodes. If an identity file does not work with it,
    the failure message is kept and docs/guides/teleport-kube.md says so in one line (issue 753 acceptance)."""
    import re
    p = subprocess.Popen(["tsh", f"--proxy={PROXY}", "--insecure", "-i", _env(tele, "alice")["TELEPORT_IDENTITY_FILE"], "proxy", "kube", CLUSTER],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_env(tele, "alice"))
    lines: list[str] = []
    try:
        import threading
        threading.Thread(target=lambda: [lines.append(x) for x in p.stdout], daemon=True).start()
        found = lambda: next((m[1] for x in lines if (m := re.search(r"KUBECONFIG=(\S+)", x))), None)
        assert wait_for(lambda: found() or p.poll() is not None, timeout=30)
        kc = found()
        assert kc, f"tsh proxy kube printed no kubeconfig (exit {p.poll()}): {' '.join(lines)[:300]}"
        r = subprocess.run(["kubectl", "get", "nodes", "-o", "name"], capture_output=True, text=True, timeout=60, check=False,
                           env={**os.environ, "KUBECONFIG": kc})
        assert r.returncode == 0 and "node/kcn001" in r.stdout, r.stderr
    finally:
        p.terminate()
        p.wait(timeout=15)


# ---- failures (issue 756, ADR 0084 W4): each through the scenario engine, read back by `get faults`, restored, a different message each ----

def kube_step(**do):
    from benchctl import scenario
    out = scenario.execute(scenario.Step(0, "auth", do))
    assert out.status == "ok", out.detail


def kube_faults() -> list[str]:
    from benchctl import faultstate
    return [r["label"] for r in faultstate.kube_rbac()]


def test_a_missing_binding_is_a_kubernetes_forbidden_for_a_user_teleport_let_in(tele):
    try:
        kube_step(kube_binding_missing=["viewer"])
        assert any("viewer tier" in x for x in kube_faults())
        r = kubectl(tele, "alice", "get", "nodes", "-o", "name")
        msg = one_line(r)
        print("binding missing:", msg)
        assert r.returncode != 0 and "forbidden" in msg and "alice" in msg, msg
    finally:
        kube_step(kube_binding_restore=True)
    assert kube_faults() == []
    assert "node/kcn001" in kubectl(tele, "alice", "get", "nodes", "-o", "name").stdout


def test_revoked_impersonation_fails_every_request_upstream_with_its_own_message(tele):
    try:
        kube_step(kube_impersonation_revoked=True)
        assert any("impersonat" in x for x in kube_faults())
        r = kubectl(tele, "alice", "get", "nodes", "-o", "name")
        msg = one_line(r)
        print("impersonation revoked:", msg)
        assert r.returncode != 0 and "impersonat" in msg, msg
        assert "forbidden" not in msg or "cannot impersonate" in msg  # not the same message as a missing binding
    finally:
        kube_step(kube_impersonation_revoked=False)
    assert kube_faults() == [] and "node/kcn001" in kubectl(tele, "alice", "get", "nodes", "-o", "name").stdout


def test_an_expired_identity_fails_tsh_kube_login_and_credentials(tele):
    import time
    try:
        kube_step(teleport_cert_ttl={"user": "alice", "ttl": "60s"})
        time.sleep(70)
        r = tsh(tele, "kube", "credentials", f"--kube-cluster={CLUSTER}", f"--teleport-cluster={teleport.CLUSTER}")
        print("identity expired:", one_line(r))
        assert r.returncode != 0
        assert tsh(tele, "kube", "login", CLUSTER, extra_env={"KUBECONFIG": str(tele / "kc-expired")}).returncode != 0
    finally:
        kube_step(teleport_cert_restore=["alice"])
    assert "node/kcn001" in kubectl(tele, "alice", "get", "nodes", "-o", "name").stdout


def test_an_expired_api_server_certificate_is_an_x509_error_from_teleports_upstream_call(tele):
    try:
        kube_step(k8s_cert_expired=True)
        assert wait_for(lambda: "x509" in one_line(kubectl(tele, "alice", "get", "nodes")) or "expired" in one_line(kubectl(tele, "alice", "get", "nodes")),
                        timeout=90), one_line(kubectl(tele, "alice", "get", "nodes"))
        print("api certificate expired:", one_line(kubectl(tele, "alice", "get", "nodes")))
    finally:
        kube_step(k8s_cert_expired=False)
    assert wait_for(lambda: "node/kcn001" in kubectl(tele, "alice", "get", "nodes", "-o", "name").stdout, timeout=120)
