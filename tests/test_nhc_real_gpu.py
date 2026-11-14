"""The real-gpu-health template (issue 437): card missing, XID, temperature over the slowdown threshold. Hermetic: nvidia-smi is a stub."""
import os
import shutil
import subprocess
from pathlib import Path

from benchctl import nhc

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "images" / "node-base" / "nhc"
TEMPLATE = nhc.SRC / "templates" / "real-gpu-health.sh"


def series(temp=40, xid=0, node="rgpu001"):
    return (f'DCGM_FI_DEV_GPU_TEMP{{node="{node}",gpu="0",Hostname="{node}"}} {temp}\n'
            f'DCGM_FI_DEV_XID_ERRORS{{node="{node}",gpu="0",Hostname="{node}"}} {xid}\n')


def run(tmp_path, metrics, smi="ok", slowdown=92):
    """Run the runner with only this check on; smi is 'ok', 'none' (no card listed) or 'absent' (no nvidia-smi)."""
    work = tmp_path / "w"
    (work / "bin").mkdir(parents=True)
    (work / "checks").mkdir()
    shutil.copy(TEMPLATE, work / "checks" / "real-gpu.sh")
    (work / "checks" / "checks.conf").write_text("real-gpu on\n")
    (work / "m.txt").write_text(metrics)
    tools = work / "tools"  # the few tools the runner needs, so a real nvidia-smi on the developer's PATH is never found
    tools.mkdir()
    for tool in ("bash", "env", "grep", "awk", "timeout", "cat", "hostname", "dirname", "basename", "sh"):
        (tools / tool).symlink_to(shutil.which(tool))
    if smi != "absent":
        listing = "GPU 0: Stub Card (UUID: GPU-1)" if smi == "ok" else ""
        stub = work / "bin" / "nvidia-smi"
        stub.write_text(f'#!/bin/sh\nif [ "$1" = -L ]; then echo "{listing}"; else echo "    GPU Slowdown Temp                 : {slowdown} C"; fi\n')
        stub.chmod(0o755)
    calls, scontrol = work / "calls.txt", work / "scontrol"
    scontrol.write_text(f'#!/bin/sh\nif [ "$1" != show ]; then echo "$@" >> {calls}; fi\n')
    scontrol.chmod(0o755)
    env = {**os.environ, "PATH": f"{work / 'bin'}:{tools}", "NHC_METRICS_FILE": str(work / "m.txt"), "NHC_SCONTROL": str(scontrol),
           "NHC_NODE": "rgpu001", "NHC_EXPECTED_GPUS": "0", "NHC_STATE_DIR": str(work), "NHC_CHECKS_DIR": str(work / "checks"), "NHC_ENV_FILE": str(work / "none")}
    r = subprocess.run(["bash", str(RUNNER)], env=env, capture_output=True, text=True, timeout=30, check=False)
    return r, (calls.read_text().strip() if calls.exists() else "")


def test_a_healthy_card_is_left_alone(tmp_path):
    r, calls = run(tmp_path, series())
    assert r.returncode == 0 and calls == ""


def test_a_missing_card_drains(tmp_path):
    r, calls = run(tmp_path, series(), smi="none")
    assert r.returncode == 1 and "reason=NHC: real GPU missing" in calls


def test_an_xid_drains_with_the_code(tmp_path):
    r, calls = run(tmp_path, series(xid=79))
    assert r.returncode == 1 and "reason=NHC: real GPU XID 79" in calls


def test_a_temperature_over_the_slowdown_threshold_drains(tmp_path):
    _, calls = run(tmp_path, series(temp=95), slowdown=92)
    assert "real GPU 95C over its slowdown 92C" in calls
    assert run(tmp_path / "b", series(temp=92), slowdown=92)[1] == ""  # at the threshold is still healthy


def test_a_node_without_nvidia_smi_skips_it(tmp_path):
    r, calls = run(tmp_path, series(xid=79, temp=99), smi="absent")
    assert r.returncode == 0 and calls == ""


def test_the_validator_accepts_the_template_as_a_check(tmp_path):
    src = tmp_path / "nhc.d"
    shutil.copytree(nhc.SRC, src)
    nhc.add("real-gpu", template="real-gpu-health", src=src)
    assert nhc.problems(src) == []
    assert "real-gpu-health" in [t["name"] for t in nhc.templates(src)]
