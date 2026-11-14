"""Kubernetes (ADR 0034, issue 232): a real control plane with KWOK nodes, read as a client reads it.

Checks, on the live containers: the five services run unprivileged, limited and labelled for the governor; the nodes are
Ready with GPU capacity; the failing fixtures show the status a kubelet would report; the read-only kubeconfig on the login
node works as the simulated user and cannot change anything; the namespace-limited one sees only its namespace; and the
scenario steps (a node reported NotReady, a crash-looping pod) are seen by the same kubectl."""
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import k8s, scenario, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
SPEC = workbench.Spec(parts=["k8s", "bmc", "bcm"], cpu_nodes=1, gpu_nodes=0, login_nodes=["ssh:slurm", "ssh:k8s"])


def docker(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)


S_LOGIN, K_LOGIN = "workbench-login-1", "workbench-login2-1"  # `ssh:slurm,ssh:k8s` (issue 380)


def as_user(user: str, *cmd: str, node: str = K_LOGIN) -> subprocess.CompletedProcess:
    """kubectl (or any command) on a login node (the k-login unless `node` says otherwise) as a simulated user, the way a client's SSH session runs it."""
    return docker("exec", "-u", user, node, "sh", "-c", " ".join(c if c.startswith("$HOME/") else shlex.quote(c) for c in cmd))  # a shell, so $HOME is the user's home


def kubectl_as(user: str, *args: str, kubeconfig: str | None = None) -> subprocess.CompletedProcess:
    return as_user(user, "kubectl", *(["--kubeconfig", f"$HOME/.kube/{kubeconfig}"] if kubeconfig else []), *args)


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    sim, prof = workbench.effective(SIM, SPEC)
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        why = " | ".join(said[-4:])
        if "REFUSED by governor" in why:
            raise AssertionError(f"ENVIRONMENT, not a code defect: the governor refused `up` (free host memory): {why}")
        raise AssertionError(f"stack.up failed: {why}")
    assert wait_for(lambda: kubectl_as("alice", "get", "nodes").returncode == 0, timeout=60), kubectl_as("alice", "get", "nodes").stderr
    yield sim
    stack.down(say=lambda m: None)


def test_the_five_services_run_unprivileged_limited_and_labelled(cluster):
    for svc in k8s.SERVICES:
        info = json.loads(docker("inspect", f"workbench-{svc}-1").stdout)[0]
        host = info["HostConfig"]
        assert info["State"]["Running"] and not host["Privileged"] and not host["CapAdd"], svc
        assert host["Memory"] == k8s.LIMITS[svc] * 1024 * 1024, svc
        assert info["Config"]["Labels"]["workbench.class"] == "P3" and info["Config"]["User"] != "0", svc  # the governor finds it by label; not root


def test_the_nodes_are_ready_with_gpu_capacity_and_the_feature_discovery_labels(cluster):
    r = kubectl_as("alice", "get", "nodes", "-o", "json")
    nodes = {n["metadata"]["name"]: n for n in json.loads(r.stdout)["items"]}
    assert set(nodes) == {n for n, _ in k8s.node_list(cluster)}, r.stderr
    assert all(any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"]["conditions"]) for n in nodes.values())
    gpu = nodes["kgpu001"]
    assert gpu["status"]["allocatable"]["nvidia.com/gpu"] == "8" and gpu["metadata"]["labels"]["nvidia.com/gpu.product"] == "NVIDIA-H200"
    assert "nvidia.com/gpu" not in nodes["kcn001"]["status"]["allocatable"]


def test_failing_fixtures_show_what_a_kubelet_would_report(cluster):
    def reasons():
        r = kubectl_as("alice", "get", "pods", "-A", "-o", "json")
        out = {}
        for p in json.loads(r.stdout)["items"]:
            cs = (p["status"].get("containerStatuses") or [{}])[0]
            out[p["metadata"]["name"].split("-")[0]] = (p["status"]["phase"], (cs.get("state", {}).get("waiting") or {}).get("reason"),
                                                          (cs.get("lastState", {}).get("terminated") or {}).get("reason"))
        return out
    assert wait_for(lambda: reasons().get("oom", (0, 0, 0))[2] == "OOMKilled", timeout=60), reasons()
    got = reasons()
    assert got["crashloop"][:2] == ("Running", "CrashLoopBackOff") and got["badimage"][:2] == ("Pending", "ImagePullBackOff")
    assert got["fails"][0] == "Failed" and got["gpu"][0] in ("Running", "Pending")
    waiting = kubectl_as("alice", "get", "pod", "gpu-waiting", "-n", "team-b", "-o", "jsonpath={.status.phase}").stdout
    assert waiting == "Pending"  # asks for 9 GPUs, a node has 8: the real scheduler leaves it unschedulable
    events = kubectl_as("alice", "get", "events", "-A", "--field-selector", "type=Warning", "-o", "jsonpath={.items[*].reason}").stdout
    assert "FailedScheduling" in events


def test_the_login_node_kubeconfig_is_the_users_own_mode_600_and_read_only(cluster):
    cfg = as_user("alice", "stat", "-c", "%U %a", "$HOME/.kube/config").stdout.split()
    assert cfg == ["alice", "600"]  # a client refuses a kubeconfig that is not the user's or is group or world readable
    assert kubectl_as("alice", "auth", "can-i", "list", "nodes").stdout.strip() == "yes"
    assert kubectl_as("alice", "auth", "can-i", "delete", "pods", "-A").stdout.strip() == "no"
    assert kubectl_as("alice", "delete", "pod", "web-nope", "-n", "team-a").returncode != 0
    assert "readyz check passed" in kubectl_as("alice", "get", "--raw", "/readyz?verbose").stdout
    assert json.loads(kubectl_as("alice", "get", "--raw", "/version").stdout)["gitVersion"] == k8s.VERSION


def test_the_namespace_limited_kubeconfig_sees_only_its_namespace(cluster):
    assert kubectl_as("alice", "auth", "can-i", "list", "pods", kubeconfig="limited.yaml").stdout.strip() == "yes"
    assert kubectl_as("alice", "auth", "can-i", "list", "nodes", kubeconfig="limited.yaml").stdout.strip() == "no"
    assert kubectl_as("alice", "get", "nodes", kubeconfig="limited.yaml").returncode != 0
    assert kubectl_as("alice", "get", "pods", "-n", "team-b", kubeconfig="limited.yaml").returncode != 0


def test_a_node_that_reports_notready_is_seen_and_comes_back(cluster):
    assert scenario.execute(scenario.Step(0, "k8s", {"down": ["kgpu002"]})).status == "ok"
    _, holds, _ = scenario.expectation({"k8s.node_ready": {"node": "kgpu002", "ready": False}})
    try:
        assert wait_for(holds, timeout=120), kubectl_as("alice", "get", "nodes").stdout
        assert "NotReady" in kubectl_as("alice", "get", "node", "kgpu002").stdout  # the client's own view
    finally:
        assert scenario.execute(scenario.Step(0, "k8s", {"up": ["kgpu002"]})).status == "ok"
    assert wait_for(lambda: scenario.expectation({"k8s.node_ready": {"node": "kgpu002"}})[1](), timeout=120)


def test_a_scenario_pod_crash_loops_and_is_deleted(cluster):
    do = {"pod": {"namespace": "team-b", "name": "loop-live", "fault": "crashloop"}}
    assert scenario.execute(scenario.Step(0, "k8s", do)).status == "ok"
    try:
        _, holds, _ = scenario.expectation({"k8s.pod_status": {"namespace": "team-b", "name": "loop-live", "reason": "CrashLoopBackOff"}})
        assert wait_for(holds, timeout=60)
    finally:
        assert scenario.execute(scenario.Step(0, "k8s", {"delete_pod": {"namespace": "team-b", "name": "loop-live"}})).status == "ok"
    assert wait_for(lambda: kubectl_as("alice", "get", "pod", "loop-live", "-n", "team-b").returncode != 0, timeout=30)


def test_the_s_login_has_slurm_and_no_kubeconfig_and_the_k_login_the_reverse(cluster):
    assert as_user("alice", "sinfo", "-h", node=S_LOGIN).returncode == 0 and as_user("alice", "test", "-e", "$HOME/.kube/config", node=S_LOGIN).returncode != 0
    assert as_user("alice", "test", "-e", "$HOME/.kube/config").returncode == 0 and as_user("alice", "sinfo", node=K_LOGIN).returncode != 0
    mounts = {n: {m["Destination"] for m in json.loads(docker("inspect", n).stdout)[0]["Mounts"]} for n in (S_LOGIN, K_LOGIN)}
    assert "/etc/slurm" in mounts[S_LOGIN] and "/run/workbench/k8s" not in " ".join(mounts[S_LOGIN])
    assert "/etc/slurm" not in mounts[K_LOGIN] and any(m.startswith("/run/workbench/k8s") for m in mounts[K_LOGIN])


def test_the_connections_ssh_command_logs_in_with_a_loaded_agent(cluster, tmp_path):
    """Issue 380: the printed command carries IdentitiesOnly, so keys in ssh-agent are not offered first (sshd gives up after 6)."""
    from webui import connections

    row = next(r for r in connections.entities(cluster) if r["id"] == "login2")
    assert "IdentitiesOnly=yes" in row["command"] and "k-login" in row["name"]
    agent = subprocess.run(["ssh-agent", "-s"], capture_output=True, text=True, check=True).stdout
    env = {**os.environ, **dict(line.split(";")[0].split("=", 1) for line in agent.splitlines() if "=" in line.split(";")[0])}
    try:
        for i in range(8):  # more keys than MaxAuthTries
            k = tmp_path / f"k{i}"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(k)], check=True, capture_output=True)
            subprocess.run(["ssh-add", str(k)], env=env, capture_output=True, check=True)
        argv = shlex.split(row["command"])
        quiet = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "BatchMode=yes"]
        bare = shlex.split(row["command"].replace(" -o IdentitiesOnly=yes", ""))
        run = lambda a: subprocess.run([*a[:1], *quiet, *a[1:], "hostname"], env=env, capture_output=True, text=True, timeout=30, cwd=ROOT, check=False)
        assert run(argv).stdout.strip() == "login2", run(argv).stderr
        res = run(bare)  # the same command without it: the failure the owner saw
        assert res.returncode != 0 and "Too many authentication failures" in res.stderr, res.stderr
    finally:
        subprocess.run(["ssh-agent", "-k"], env=env, capture_output=True, check=False)


def test_the_page_snapshot_groups_the_kubernetes_nodes_and_types_the_logins(cluster, monkeypatch):
    from webui import graph
    from webui.cluster import Cluster

    monkeypatch.setattr("webui.cluster.k8s_built", lambda: True)  # tests/conftest.py turns it off so a developer's build does not leak in
    workbench.save(SPEC)  # the page reads the saved selection (SAVED is a temporary file in tests, tests/conftest.py)
    snap = Cluster(cluster).snapshot()
    kn = [n for n in snap["nodes"] if n["kind"] == "k8s"]
    assert {n["name"] for n in kn} == {"kcn001", "kcn002", "kgpu001", "kgpu002"}, snap["nodes"]
    assert {n["state"] for n in kn} == {"ok"}, kn
    assert {lg["name"]: lg["kind"] for lg in snap["logins"]} == {"login": "slurm", "login2": "k8s"}
    g = graph.build(snap["services"], snap["nodes"], snap["logins"])
    ids = {e["id"] for e in g["edges"]}
    assert "login>nodes:jump" in ids and "login2>k8s-nodes:kubectl" in ids and "login2>nodes:jump" not in ids and "login>k8s-nodes:kubectl" not in ids


def test_the_kubernetes_nodes_have_a_bmc_a_bcm_entry_and_gpu_metrics(cluster):
    """Issue 380: a Kubernetes node is a machine. Rules per node group decide which get a BMC (here every group)."""
    from urllib.request import Request, urlopen

    from benchctl import addressing

    plan = addressing.plan(cluster)
    for n in ("kcn001", "kgpu002"):
        assert wait_for(lambda n=n: docker("inspect", "-f", "{{.State.Running}}", f"workbench-bmc-{n}-1").stdout.strip() == "true", timeout=30), n
    cred = (ROOT / "compose/generated/secrets/bmc_credentials").read_text().split(":")[-1].strip()
    import base64
    import ssl

    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    req = Request(f"https://{plan['bmc']['kgpu001']}/redfish/v1/Systems/1", headers={"Authorization": "Basic " + base64.b64encode(f"admin:{cred}".encode()).decode()})
    assert wait_for(lambda: json.load(urlopen(req, context=ctx, timeout=5)).get("HostName") == "kgpu001", timeout=30)
    out = as_user("alice", "cmsh", "-c", "device; list", node=S_LOGIN).stdout
    assert all(f"{n} " in out for n in ("kcn001", "kcn002", "kgpu001", "kgpu002")) and "k8s-gpu" in out and "k8s-cpu" in out, out


def test_the_api_audit_log_is_on_and_the_capture_reads_a_nodes_own_events(cluster):
    """issue 505: the audit log is enabled (metadata only, narrow) and `benchctl get capture` keeps the events about one node."""
    import sys

    logs = docker("logs", "--tail", "400", "workbench-k8s-apiserver-1").stdout + docker("logs", "--tail", "400", "workbench-k8s-apiserver-1").stderr
    assert '"auditID"' in logs, "the audit log is not on"
    assert '"requestObject"' not in logs and '"responseObject"' not in logs  # metadata only: no request or response bodies
    assert '"resource":"secrets"' not in logs and '"resource":"configmaps"' not in logs  # not in the policy
    node = k8s.node_list(SIM)[0][0]
    r = subprocess.run([sys.executable, "-m", "benchctl.cli", "get", "capture", node, "--seconds", "30"], cwd=ROOT, capture_output=True, text=True, timeout=90, check=False)
    msgs = [json.loads(ln) for ln in r.stdout.splitlines() if ln.startswith("{")]
    assert r.returncode == 0, r.stderr
    assert msgs, "KWOK's lease renewals and node status updates should have been seen within 30 s"
    assert all(m["object"].endswith(node) or m["type"].split()[-1].startswith("pods") for m in msgs)  # only this node's lease and node object, or its pods
    assert any("leases" in m["type"] or "nodes" in m["type"] for m in msgs) and all(m["who"] and m["result"] for m in msgs)


def test_prometheus_scrapes_the_control_plane_with_the_metrics_only_token(cluster):
    """Issue 458: a job per selected Kubernetes part, generated by `up`; the API server one reads only the `metrics` user's token."""
    from helpers import prom_up, wait_for
    jobs = ["k8s-apiserver", "k8s-etcd", "k8s-scheduler", "k8s-controller-manager", "kwok-controller"]
    assert wait_for(lambda: all(set(prom_up(j).values()) == {"1"} for j in jobs), timeout=120), {j: prom_up(j) for j in jobs}
    got = (ROOT / "compose/generated/secrets/prometheus").iterdir()
    assert sorted(p.name for p in got) == ["k8s-ca.crt", "k8s-metrics.token"]  # no admin or viewer token reaches Prometheus
