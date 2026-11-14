"""Declared storage and the shared scratch volume on the live min stack (issue 461, ADR 0078). Run: `./scripts/verify.sh --unit storage`.

Asserts on containers: one directory seen from the login node and a compute node, a Slurm job's output file visible from the login node,
`df` and `lfs` output on a node, the exporter's `node_filesystem_*` series, and the three scenarios' effect on all of them.
No monitoring app is connected."""
import json
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, exporter_up, release, wait_for

ROOT = Path(__file__).resolve().parents[2]
LOGIN, CN1, CN2, GPU1 = "workbench-login-1", "workbench-cn001-1", "workbench-cn002-1", "workbench-gpu001-1"
EXPORTER = "http://127.0.0.1:9400"


def run(c: str, *cmd: str, user: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", *(["-u", user] if user else []), c, *cmd], capture_output=True, text=True, timeout=60, check=False)


def inject(node: str, storage: dict) -> None:
    req = urllib.request.Request(f"{EXPORTER}/sim/inject", json.dumps({"node": node, "storage": storage}).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def series(metric: str, node: str, mount: str = "/scratch") -> float | None:
    text = urllib.request.urlopen(f"{EXPORTER}/metrics?node={node}", timeout=5).read().decode()
    for ln in text.splitlines():
        if ln.startswith(metric + "{") and f'mountpoint="{mount}"' in ln:
            return float(ln.rsplit(" ", 1)[1])
    return None


@pytest.fixture(scope="module")
def live():
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(exporter_up, timeout=60)
    assert wait_for(lambda: run(LOGIN, "test", "-d", "/scratch/alice").returncode == 0, timeout=60), "the entrypoint makes /scratch/alice"
    yield
    for n in ("cn001", "cn002"):  # a shared module leaves the cluster as it found it
        inject(n, {"mount": "/scratch", "reset": True})
    run(LOGIN, "rm", "-rf", "/scratch/alice/live-test", user="alice")
    release()


def test_a_file_written_on_a_node_is_seen_on_the_login_node_and_another_node_with_the_users_ownership(live):
    assert run(CN1, "sh", "-c", "mkdir -p /scratch/alice/live-test && echo dataset > /scratch/alice/live-test/shard-000", user="alice").returncode == 0
    for c in (LOGIN, GPU1, CN2):
        r = run(c, "cat", "/scratch/alice/live-test/shard-000")
        assert r.stdout.strip() == "dataset", (c, r.stderr)
    owner = run(LOGIN, "stat", "-c", "%U", "/scratch/alice/live-test/shard-000").stdout.strip()
    assert owner == "alice"
    assert run(CN1, "ls", "/scratch/bob", user="alice").returncode != 0  # a user's directory is mode 750


def test_a_slurm_jobs_output_file_appears_on_the_login_node(live):
    r = run(LOGIN, "sbatch", "--parsable", "-o", "/scratch/alice/live-test/job-%j.out", "--wrap", "hostname; echo done", user="alice")
    assert r.returncode == 0, r.stderr
    job = r.stdout.strip().split(";")[0]
    assert wait_for(lambda: run(LOGIN, "cat", f"/scratch/alice/live-test/job-{job}.out").stdout.strip().endswith("done"), timeout=90), run(LOGIN, "squeue").stdout


def test_df_on_a_node_shows_the_declared_mounts_in_gnu_layout(live):
    out = run(CN1, "df", "-hT", "/scratch", "/home").stdout.split("\n")
    assert out[0].split()[:3] == ["Filesystem", "Type", "Size"], out
    rows = {ln.split()[-1]: ln.split() for ln in out[1:] if ln}
    assert rows["/scratch"][:3] == ["mgs01@tcp:/scratch", "lustre", "500T"] and rows["/home"][:3] == ["nas01:/export/home", "nfs4", "20T"]
    inodes = run(LOGIN, "df", "-i", "/scratch").stdout.split("\n")
    assert inodes[0].split()[:5] == ["Filesystem", "Inodes", "IUsed", "IFree", "IUse%"] and inodes[1].split()[-2:] == ["25%", "/scratch"]
    assert run(CN1, "df", "/").returncode == 0  # a path that is not declared still gets the real df
    assert float(series("node_filesystem_size_bytes", "cn001")) == 500 * 1024**4


def test_lfs_df_and_quota_answer_for_the_lustre_mount_only(live):
    out = run(CN1, "lfs", "df", "-h", "/scratch").stdout
    assert "scratch-MDT0000_UUID" in out and "filesystem_summary:" in out and "scratch-OST0003_UUID" in out
    assert run(CN1, "lfs", "df", "/home").returncode == 1
    q = run(CN1, "lfs", "quota", "-u", "alice", "-h", "/scratch", user="alice")
    assert q.returncode == 0 and "Disk quotas for usr alice" in q.stdout and "*" not in q.stdout, q.stderr


def test_scenario_disk_full_changes_df_lfs_and_the_series_everywhere_then_resets(live):
    inject("cn001", {"mount": "/scratch", "used_pct": 99, "inodes_used_pct": 90})
    assert wait_for(lambda: re.search(r"\b99%", run(GPU1, "df", "-h", "/scratch").stdout) is not None, timeout=20), run(GPU1, "df", "-h", "/scratch").stdout
    assert "90%" in run(LOGIN, "df", "-i", "/scratch").stdout
    assert series("node_filesystem_free_bytes", "cn002") == pytest.approx(500 * 1024**4 * 0.01, rel=0.001)
    inject("cn001", {"mount": "/scratch", "reset": True})
    assert "40%" in run(CN1, "df", "-h", "/scratch").stdout


def test_scenario_quota_exceeded_marks_the_user_and_stale_mount_breaks_df_on_one_node(live):
    inject("cn001", {"mount": "/scratch", "user": "alice", "used_gb": 20500})
    q = run(CN1, "lfs", "quota", "-u", "alice", "/scratch", user="alice")
    assert "*" in q.stdout and "6d23h59m" in q.stdout, q.stdout
    inject("cn001", {"mount": "/scratch", "reset": True})
    inject("cn002", {"mount": "/scratch", "stale": True})
    bad = run(CN2, "df", "/scratch")
    assert bad.returncode == 1 and "Cannot send after transport endpoint shutdown" in bad.stderr, bad.stderr
    assert run(CN1, "df", "/scratch").returncode == 0  # one node only
    assert series("node_filesystem_size_bytes", "cn002") is None and series("node_filesystem_device_error", "cn002") == 1.0
    inject("cn002", {"mount": "/scratch", "reset": True})
    assert wait_for(lambda: run(CN2, "df", "/scratch").returncode == 0, timeout=20)
