"""Job usage from /proc and Slurm's own metrics against the live min stack (issue 459, ADR 0074). Run:
`./scripts/verify.sh --unit slurm_metrics`.

Asserts on the real parts: `sstat` inside an unprivileged node shows non-zero memory for a running job, slurmctld's own endpoint
answers Prometheus without a token, and draining a node raises `NodeDrained` (and so a Jira mock ticket, ADR 0072)."""
import json
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, prom_up, release, wait_for

ROOT = Path(__file__).resolve().parents[2]
PROM, JIRA = "http://127.0.0.1:9090", "http://127.0.0.1:8080"
JOBS = ("slurm-jobs", "slurm-nodes", "slurm-partitions", "slurm-scheduler")
HOG = "python3 -c \"x = b'a' * (60 * 1024 * 1024); import time; time.sleep(150)\""


def sh(*cmd: str, user: str | None = "alice", container: str = "workbench-login-1") -> subprocess.CompletedProcess:
    pre = ["docker", "exec", *(["-u", user] if user else []), container]
    return subprocess.run([*pre, *cmd], capture_output=True, text=True, timeout=60, check=False)


def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read() or b"null")
    except (OSError, ValueError):
        return None


def query(expr: str) -> list[dict]:
    return (get(f"{PROM}/api/v1/query?query={urllib.parse.quote(expr)}") or {}).get("data", {}).get("result", [])


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    assert wait_for(lambda: "cn001" in sh("sinfo", "-h", "-N", "-t", "idle", "-o", "%N").stdout, timeout=90)
    yield
    sh("scancel", "-u", "alice")
    sh("scontrol", "update", "nodename=cn001", "state=resume", user=None, container="workbench-slurmctld-1")
    release()


def to_bytes(s: str) -> float:
    s = s.strip()
    return float(s[:-1]) * {"K": 1024, "M": 1024**2, "G": 1024**3}[s[-1]] if s and s[-1] in "KMG" else float(s or 0)


def test_a_running_job_shows_cpu_and_memory_usage_in_sstat(cluster):
    r = sh("sbatch", "-D", "/tmp", "-p", "cpu", "--nodelist=cn002", f"--wrap={HOG}")
    assert r.returncode == 0, r.stderr
    job = r.stdout.split()[-1]

    def usage() -> float:
        out = sh("sstat", "-j", f"{job}.batch", "-n", "-P", "-o", "MaxRSS").stdout.strip()
        return to_bytes(out.splitlines()[0]) if out else 0

    assert wait_for(lambda: usage() > 20 * 1024**2, timeout=120), f"sstat MaxRSS stayed at {usage()} (jobacct_gather/linux not working in the node?)"


def test_slurms_own_endpoint_answers_prometheus_for_all_four_families(cluster):
    assert wait_for(lambda: all(set(prom_up(j).values()) == {"1"} for j in JOBS), timeout=90), {j: prom_up(j) for j in JOBS}
    assert wait_for(lambda: query("slurm_nodes_idle") != [], timeout=60)
    assert wait_for(lambda: float(query("slurm_jobs_running")[0]["value"][1]) >= 1, timeout=60), query("slurm_jobs_running")


def test_draining_a_node_raises_node_drained_and_a_ticket_and_resume_clears_it(cluster):
    r = sh("scontrol", "update", "nodename=cn001", "state=drain", "reason=live-test", user=None, container="workbench-slurmctld-1")
    assert r.returncode == 0, r.stderr

    def firing() -> bool:
        alerts = (get(f"{PROM}/api/v1/alerts") or {}).get("data", {}).get("alerts", [])
        return any(a["labels"]["alertname"] == "NodeDrained" and a["state"] == "firing" for a in alerts)

    assert wait_for(firing, timeout=150), query("slurm_nodes_drain")
    q = urllib.parse.quote("labels = alertname-NodeDrained")
    assert wait_for(lambda: len((get(f"{JIRA}/rest/api/2/search?jql={q}") or {}).get("issues", [])) == 1, timeout=120)
    sh("scontrol", "update", "nodename=cn001", "state=resume", user=None, container="workbench-slurmctld-1")
    assert wait_for(lambda: not firing(), timeout=120)
    req = urllib.request.Request(f"{JIRA}/sim/tickets", data=json.dumps({"action": "transition", "jql": "labels = alertname-NodeDrained", "to": "Resolved"}).encode(), method="POST")
    urllib.request.urlopen(req, timeout=10).read()  # leave the Jira mock as found
