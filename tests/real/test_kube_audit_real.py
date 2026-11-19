"""The per-user Kubernetes request counter against the live stack (issue 384, ADR 0087): real kubectl calls as two users, read through the
governor's `kube-audit` verb (as the page does) and counted by the page's KubeAudit. Written without a live run in the authoring session.

Needs a governor with `--workdir` (`./scripts/benchctl start`) that knows the verb: a root service started before this change must be
restarted first. A governor at L2 or above refuses the verb (asserted in the unit tests, not here, so this test never needs pressure)."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import skip_no_governor, wait_for

from benchctl import checks, k8s, kubeaudit, stack, workbench
from webui.kubeaudit import SCOPE, KubeAudit

ROOT = Path(__file__).resolve().parents[2]
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
SPEC = workbench.Spec(parts=["k8s", "bmc", "bcm"], cpu_nodes=1, gpu_nodes=0, login_nodes=["ssh:slurm", "ssh:k8s"])
K_LOGIN = "workbench-login2-1"


def kubectl(kubeconfig: str, *args: str) -> subprocess.CompletedProcess:
    cfg = f"--kubeconfig $HOME/.kube/{kubeconfig} " if kubeconfig else ""
    return subprocess.run(["docker", "exec", "-u", "alice", K_LOGIN, "sh", "-c", f"kubectl {cfg}{' '.join(args)}"], capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    sim, prof = workbench.effective(SIM, SPEC)
    said: list[str] = []
    if not stack.up(sim, "workbench", say=said.append, available=64 * 1024**3, prof=prof):
        raise AssertionError(f"stack.up failed: {' | '.join(said[-4:])}")
    assert wait_for(lambda: kubectl("", "get", "nodes").returncode == 0, timeout=90)
    yield sim
    stack.down(say=lambda m: None)


def ask(req: dict) -> dict:
    try:
        return checks.governor_request(req, checks.GOV_SOCKET, timeout=20)
    except OSError:
        skip_no_governor()


def test_the_governor_returns_the_audit_records_and_the_page_counts_them_per_user(cluster):
    res = ask({"cmd": "kube-audit", "since": 0})
    if not res["ok"] and "unknown command" in res.get("error", ""):
        pytest.skip("the running governor predates the kube-audit verb: restart it (root service: sudo systemctl restart workbench-governor)")
    assert res["ok"] and res["class"] == "P5", res
    assert kubectl("", "get", "pods", "-A").returncode == 0 and kubectl("", "get", "nodes").returncode == 0  # the default kubeconfig: the viewer

    def counted():
        got = ask({"cmd": "kube-audit", "since": 0})
        return got["ok"] and any(e["user"] == "viewer" and e["resource"] in ("pods", "nodes") for e in got["events"])
    assert wait_for(counted, timeout=30), "the viewer's kubectl calls did not reach the audit read"
    got = ask({"cmd": "kube-audit", "since": 0})
    assert len(got["events"]) <= kubeaudit.MAX_EVENTS
    assert all(set(e) == {"id", "t", "user", "verb", "resource", "object", "code"} for e in got["events"])  # metadata only, never a body
    assert all(e["resource"].split("/")[0] in {"nodes", "pods", "leases"} for e in got["events"])  # the policy's scope: nodes and pods only

    page = KubeAudit(ask)
    page.poll()
    s = page.summary()
    assert s["measured"] and s["scope"] == SCOPE and any(u["user"] == "viewer" and u["per_min"] >= 1 for u in s["users"]), s
    assert k8s.api_up()  # the API stayed up while it was read
