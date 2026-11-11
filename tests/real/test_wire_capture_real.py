"""On-demand wire capture of a Slurm node against the live stack (issue 505, ADR 0042): real tcpdump in a real node, decoded by the parser."""
import json
import subprocess
import sys
import threading
from pathlib import Path

import yaml
from helpers import bring_up, release, wait_for

from benchctl import slurm_msgtypes

ROOT = Path(__file__).resolve().parents[2]


def srun(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-u", "alice", "workbench-login-1", *cmd], capture_output=True, text=True, timeout=60, check=False)


def test_a_real_job_launch_shows_as_decoded_slurm_messages_at_the_node():
    import os

    os.chdir(ROOT)
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    try:
        assert wait_for(lambda: "cn001" in srun("sinfo", "-h", "-N", "-t", "idle", "-o", "%N").stdout, timeout=90)
        proc = subprocess.Popen([sys.executable, "-m", "benchctl.cli", "get", "capture", "cn001", "--seconds", "40"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        got: list[dict] = []
        threading.Thread(target=lambda: [got.append(json.loads(ln)) for ln in proc.stdout if ln.startswith("{")], daemon=True).start()
        for _ in range(2):  # launching tasks on the node sends RPCs to its slurmd (6818)
            assert srun("srun", "-w", "cn001", "hostname").returncode == 0
        assert wait_for(lambda: any(m["type"] != "unknown" for m in got), timeout=30), f"nothing decoded: {got[:3]}"
        proc.terminate()
        known = [m for m in got if m["type"] != "unknown"]
        assert all(m["type"] in slurm_msgtypes.TYPES.values() and m["type_id"] in slurm_msgtypes.TYPES for m in known)
        assert any(m["dir"] == "in" and m["peer_name"] in ("slurmctld", "login", "controller") for m in known), known[:3]
        assert any(m["kind"] == "request" for m in known) and all(m["size"] > 14 for m in known)
        assert len(known) >= len(got) / 2, "most real traffic should parse: the header format or the pinned version is off"
        assert not any("body" in m for m in got)  # nothing from a body
    finally:
        release()
