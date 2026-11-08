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


def docker(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)


def as_user(user: str, *cmd: str) -> subprocess.CompletedProcess:
    """kubectl (or any command) on the login node as a simulated user, the way a client's SSH session runs it."""
    return docker("exec", "-u", user, "workbench-login-1", "sh", "-c", " ".join(c if c.startswith("$HOME/") else shlex.quote(c) for c in cmd))  # a shell, so $HOME is the user's home


def kubectl_as(user: str, *args: str, kubeconfig: str | None = None) -> subprocess.CompletedProcess:
    return as_user(user, "kubectl", *(["--kubeconfig", f"$HOME/.kube/{kubeconfig}"] if kubeconfig else []), *args)


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    spec = workbench.Spec(parts=["k8s"], cpu_nodes=1, gpu_nodes=0, login_nodes=["ssh"])
    sim, prof = workbench.effective(SIM, spec)
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
