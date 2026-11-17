"""Every read a monitoring client makes against Kubernetes, from the k-login, as the cluster-wide viewer and as the limited user
(issue 762, ADR 0084). The reads are the catalogue in `benchctl/k8sreads.py` (this repository's own words, listed in
docs/guides/client-testing.md); each one is run over `docker exec -u alice k-login kubectl ...`, the way a client's SSH session runs it, and
its outcome and the shape of its answer are asserted. Also the hop: `ssh k-login kubectl ...` from another node, and one clear error for a
wrong host.

The metrics API answers 200 for the viewer and is refused for the limited user (issue 382, metrics-server). Asserted as "not served": the
Volcano and Kubeflow groups (issues 765 and 390 flip them). The kubeconfig fixtures (issue 767) are checked for owner, group and mode in every
simulated user's ~/.kube and run with kubectl without a terminal. Written without a live run in the authoring session."""
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import wait_for

from benchctl import k8s, k8sreads, kubeconfigs, stack, workbench

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
def metrics_ready() -> None:
    """metrics-server answers only after two scrapes of every node (--metric-resolution=15s); until then the APIService says 503, and
    API discovery fails too. Waited for only by the tests that need it, so a broken metrics-server fails those and nothing else."""
    assert wait_for(lambda: run_read(k8sreads.BY_KEY["node-usage"], "viewer").returncode == 0, timeout=180), \
        "metrics-server never answered: " + run_read(k8sreads.BY_KEY["node-usage"], "viewer").stderr.strip()[-300:]


METRICS_READS = {"metrics-probe", "node-usage"}


def test_each_read_has_the_outcome_and_shape_the_client_needs(cluster, read, role):
    if read.key in METRICS_READS or read.key.startswith("api-group-"):
        metrics_ready()
    api_version = k8s.version(cluster)
    r = run_read(read, role)
    got = k8sreads.classify(read, r.returncode, r.stdout, r.stderr)
    want = read.viewer if role == "viewer" else read.limited
    assert got in want, f"{read.key} as {role}: outcome {got!r}, expected one of {want}; stderr: {r.stderr.strip()[-300:]}"
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


def test_kubectl_logs_honours_tail_limit_bytes_timestamps_and_previous(cluster):
    """Issue 760 (ADR 0084): KWOK v0.8.0 applies the kubelet's log options to the canned file (pkg/kwok/server/debugging_logs.go)."""
    def logs(args: str) -> subprocess.CompletedProcess:
        return docker_exec_as("alice", K_LOGIN, f"kubectl logs {args}")
    full = logs("sized -n team-a")
    assert full.returncode == 0 and len(full.stdout.splitlines()) >= 3, full.stderr
    assert logs("sized -n team-a --tail=2").stdout.splitlines() == full.stdout.splitlines()[-2:]
    assert len(logs("sized -n team-a --limit-bytes=20").stdout.encode()) <= 20
    stamped = logs("sized -n team-a --timestamps").stdout.splitlines()
    assert stamped and all(re.match(k8sreads.TIMESTAMPED, ln) for ln in stamped)
    now = logs("crashloop -n team-b --timestamps").stdout
    before = logs("crashloop -n team-b --previous --timestamps").stdout
    assert "failing" in now and "failing" in before and now != before  # two runs: the current one and the one before
    nothing = logs("sized -n team-a --previous")  # no run before: an error, as a kubelet with no terminated container gives
    assert nothing.returncode != 0, nothing.stdout


def test_kubectl_top_lists_every_kwok_node_and_the_pod_usage_its_annotations_give(cluster):
    """Issue 382: metrics-server scrapes KWOK's /metrics/nodes/<node>/metrics/resource; a pod's usage is its kwok.x-k8s.io/usage-* annotations."""
    metrics_ready()
    top = docker_exec_as("alice", K_LOGIN, "kubectl top nodes --no-headers")
    assert top.returncode == 0, top.stderr
    assert {ln.split()[0] for ln in top.stdout.splitlines() if ln.strip()} == {n for n, _ in k8s.node_list(cluster)}

    def sized():
        r = docker_exec_as("alice", K_LOGIN, "kubectl top pod sized -n team-a --no-headers")
        return r.stdout.split() if r.returncode == 0 else []
    def near() -> bool:  # memory is a gauge (exact); CPU is a rate over KWOK's cumulative counter, so allow a little either side
        got = sized()
        return len(got) >= 3 and got[2] == "200Mi" and got[1].endswith("m") and 340 <= int(got[1][:-1]) <= 360
    assert wait_for(near, timeout=90), sized()  # the fixture's annotations (k8s/fixtures/20-signals.yaml): 350m, 200Mi
    limited = docker_exec_as("alice", K_LOGIN, "kubectl --kubeconfig $HOME/.kube/limited.yaml top pod -n team-a")
    assert limited.returncode != 0 and "forbidden" in limited.stderr.lower(), limited.stderr  # no metrics.k8s.io rule in the namespace role


SIM_USERS = [u["name"] for u in SIM["users"]]


def _stat(user: str, rel: str) -> list[str]:
    return docker_exec_as(user, K_LOGIN, f"stat -c '%U %G %a' $HOME/.kube/{rel}").stdout.split()


def test_every_kubeconfig_fixture_has_the_stated_owner_group_and_mode_for_every_user(cluster):
    """Issue 767: real files with real owners and modes, in each user's ~/.kube; `config` and `limited.yaml` stay the user's own, mode 600."""
    assert len(SIM_USERS) >= 2, "a foreign-owned file needs a second simulated user"
    for me in SIM_USERS:
        other = next(u for u in SIM_USERS if u != me)
        for f in kubeconfigs.FIXTURES:
            owner = {"user": me, "other": other, "root": "root"}[f.owner]
            group = {"user": me, "other": other, "root": "root"}.get(f.group, f.group)
            assert _stat(me, f.name) == [owner, group, f.mode], (me, f.name, _stat(me, f.name))
        for rel in kubeconfigs.SCRIPTS:
            assert _stat(me, rel) == [me, me, "700"], (me, rel)
        assert _stat(me, "plugins") == [me, me, "700"]


def test_the_default_kubeconfig_is_untouched_by_the_fixtures(cluster):
    for me in SIM_USERS:
        assert _stat(me, "config") == [me, me, "600"] and _stat(me, "limited.yaml") == [me, me, "600"]
        same = docker_exec_as(me, K_LOGIN, "cmp $HOME/.kube/config /run/workbench/k8s/viewer.kubeconfig && cmp $HOME/.kube/limited.yaml /run/workbench/k8s/limited.kubeconfig")
        assert same.returncode == 0, same.stderr


def test_kubectl_works_with_every_fixture_file_except_the_browser_sign_in(cluster):
    """The token file, the loose and foreign files (kubectl does not judge them; a client does) and the exec plugin that prints a token work;
    the plugin that needs a browser is refused by kubectl itself without a terminal (interactiveMode Always: Kubernetes authentication docs)."""
    for f in kubeconfigs.FIXTURES:
        r = docker_exec_as("alice", K_LOGIN, f"kubectl --kubeconfig $HOME/.kube/{f.name} get nodes -o name")
        if f.kubectl == "ok":
            assert r.returncode == 0 and "node/kgpu001" in r.stdout, (f.name, r.stderr)
        else:
            assert r.returncode != 0 and kubeconfigs.INTERACTIVE_ERROR in r.stderr.lower(), (f.name, r.stdout, r.stderr)
    # the plugin itself, run by hand, is the "plugin's own error" a person at a terminal would see
    own = docker_exec_as("alice", K_LOGIN, "$HOME/.kube/plugins/browser-signin.sh")
    assert own.returncode == 1 and "browser" in own.stderr

