"""Every read a monitoring client makes against Kubernetes, from the k-login, as the cluster-wide viewer and as the limited user
(issue 762, ADR 0084). The reads are the catalogue in `benchctl/k8sreads.py` (this repository's own words, listed in
docs/guides/client-testing.md); each one is run over `docker exec -u alice k-login kubectl ...`, the way a client's SSH session runs it, and
its outcome and the shape of its answer are asserted. Also the hop: `ssh k-login kubectl ...` from another node, and one clear error for a
wrong host.

Recorded, not failed: the metrics API probe answers 404 today (it becomes 200 with issue 382). Asserted as "not served": the Volcano and
Kubeflow groups (issues 765 and 390 flip them). Written without a live run in the authoring session."""
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import k8s, k8sreads, stack, workbench

ROOT = Path(__file__).resolve().parents[2]
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
SPEC = workbench.Spec(parts=["k8s", "bmc", "bcm"], cpu_nodes=1, gpu_nodes=0, login_nodes=["ssh:slurm", "ssh:k8s"])
S_LOGIN, K_LOGIN = "workbench-login-1", "workbench-login2-1"  # `ssh:slurm,ssh:k8s` (issue 380)
HOP_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10"]


def docker_exec_as(user: str, node: str, script: str) -> subprocess.CompletedProcess:
    """`script` in a shell on `node` as `user` (a shell, so $HOME is the user's home)."""
    return subprocess.run(["docker", "exec", "-u", user, node, "sh", "-c", script], capture_output=True, text=True, timeout=90, check=False)


def run_read(read: k8sreads.Read, role: str, user: str = "alice") -> subprocess.CompletedProcess:
    kc = k8sreads.KUBECONFIG[role]
    cfg = f"--kubeconfig $HOME/.kube/{kc} " if kc else ""
    return docker_exec_as(user, K_LOGIN, f"kubectl {cfg}{' '.join(shlex.quote(a) for a in read.args)}")


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
    first = k8sreads.BY_KEY["nodes"]
    assert wait_for(lambda: run_read(first, "viewer").returncode == 0, timeout=90), run_read(first, "viewer").stderr
    # the fixtures (pods, events, Deployments) are applied after the API answers; the pod reads below need them
    assert wait_for(lambda: "sized" in run_read(k8sreads.BY_KEY["pods-ns"], "viewer").stdout, timeout=120)
    yield sim
    stack.down(say=lambda m: None)


def _ids():
    return [(r, role) for r in k8sreads.READS for role in k8sreads.ROLES]


@pytest.mark.parametrize("read,role", _ids(), ids=lambda v: v if isinstance(v, str) else v.key)
def test_each_read_has_the_outcome_and_shape_the_client_needs(cluster, read, role):
    api_version = k8s.version(cluster)
    r = run_read(read, role)
    got = k8sreads.classify(read, r.returncode, r.stdout, r.stderr)
    want = read.viewer if role == "viewer" else read.limited
    assert got in want, f"{read.key} as {role}: outcome {got!r}, expected one of {want}; stderr: {r.stderr.strip()[-300:]}"
    if read.key == "metrics-probe":
        print(f"metrics API probe as {role}: {got} (404 today, 200 with issue 382)")  # recorded, not failed
    if got == "ok":
        assert not (problems := k8sreads.shape_problems(read, role, r.stdout, api_version)), f"{read.key} as {role}: {problems}"


def test_the_kubelet_version_on_every_node_is_the_api_servers(cluster):
    """Issue 766 a: KWOK's own `kwok-v0.8.0` has no vX.Y prefix, so a client could not judge version skew."""
    api = json.loads(run_read(k8sreads.BY_KEY["version"], "viewer").stdout)["gitVersion"]
    nodes = json.loads(run_read(k8sreads.BY_KEY["nodes"], "viewer").stdout)["items"]
    assert {n["status"]["nodeInfo"]["kubeletVersion"] for n in nodes} == {api}


def test_a_gpu_node_has_gpu_capacity_allocatable_and_feature_discovery_labels(cluster):
    nodes = {n["metadata"]["name"]: n for n in json.loads(run_read(k8sreads.BY_KEY["nodes"], "viewer").stdout)["items"]}
    gpu = nodes["kgpu001"]
    assert gpu["status"]["capacity"]["nvidia.com/gpu"] == gpu["status"]["allocatable"]["nvidia.com/gpu"] == "8"
    assert all(lb in gpu["metadata"]["labels"] for lb in k8sreads.GFD_LABELS) and gpu["metadata"]["labels"][k8sreads.POOL_LABEL]
    assert "nvidia.com/gpu" not in nodes["kcn001"]["status"]["capacity"]


def test_the_viewer_sees_the_warning_the_scheduler_wrote(cluster):
    def reasons():
        r = run_read(k8sreads.BY_KEY["events-warning"], "viewer")
        return {e["reason"] for e in json.loads(r.stdout)["items"]} if r.returncode == 0 else set()
    assert wait_for(lambda: "FailedScheduling" in reasons(), timeout=60), reasons()  # gpu-waiting asks for 9 GPUs, a node has 8


def test_the_limited_user_is_refused_cluster_wide_and_served_in_its_namespace(cluster):
    """The two sides of one namespace limit, by exit code and by the text a client shows."""
    r = run_read(k8sreads.BY_KEY["pods-all"], "limited")
    assert r.returncode != 0 and "forbidden" in r.stderr.lower() and "cannot list resource" in r.stderr.lower(), r.stderr
    own = json.loads(run_read(k8sreads.BY_KEY["pods-ns"], "limited").stdout)["items"]
    assert own and {p["metadata"]["namespace"] for p in own} == {k8sreads.LIMITED_NS}


def test_the_hop_runs_kubectl_on_the_k_login_from_another_node_as_a_client_does(cluster):
    """The client may run kubectl on a 'Kubernetes host' reached by `ssh <host>` from the node it is connected to (here the s-login)."""
    opts = " ".join(HOP_OPTS)
    r = docker_exec_as("alice", S_LOGIN, f"ssh {opts} login2 kubectl version -o json")
    assert r.returncode == 0, ("the hop failed: " + r.stderr.strip()[-400:] + " (ADR 0084: the user's key on the first node is the unverified part)")
    assert json.loads(r.stdout)["serverVersion"]["gitVersion"] == k8s.version(cluster)
    nodes = docker_exec_as("alice", S_LOGIN, f"ssh {opts} login2 kubectl get nodes -o name")
    assert nodes.returncode == 0 and "node/kgpu001" in nodes.stdout, nodes.stderr


def test_a_wrong_host_gives_one_clear_error(cluster):
    r = docker_exec_as("alice", S_LOGIN, f"ssh {' '.join(HOP_OPTS)} no-such-k-login kubectl version")
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert r.returncode == 255 and len(lines) == 1 and "no-such-k-login" in lines[0], r.stderr
