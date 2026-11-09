"""The real GPU, opt-in (the `real-gpu` part, issue 428, SPEC C-23): the host's own NVIDIA card with the real dcgm-exporter.

One container gets the card as a CDI device (`nvidia.com/gpu=all`, the NVIDIA container toolkit): default capabilities, no
`privileged`, no `cap_add`. The same image as the mock exporter (`components/dcgm-exporter`) runs with no mock profile, so it reads the real
driver: real temperature, power, utilisation and framebuffer, the real model name. It sits next to the simulated GPUs, never in place of
them. Refused with one message when the machine has no NVIDIA driver or no CDI spec."""
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

SERVICE = "real-gpu"
PORT = 9403                    # published on 127.0.0.1 (the mock exporter 9402, the farm 9400)
LIMIT_MB = 640                 # measured 481 MB with the real driver: the host engine alone takes 404 MB (125 MB in total with the mock)
NODE = "rgpu001"              # the Slurm node on the card (issue 428, slice 2): a mode of this part, not a part of its own
HOSTNAME = NODE                # DCGM's Hostname label: the exporter and the node are one machine to a client
PARTITION = "real-gpu"         # its own Slurm partition, so no simulated job lands on the card by accident (owner decision)

DEVICE = "nvidia.com/gpu=all"  # CDI name, from /var/run/cdi/nvidia.yaml (`nvidia-ctk cdi generate`)
CDI_SPECS = (Path("/var/run/cdi/nvidia.yaml"), Path("/etc/cdi/nvidia.yaml"))
HINT = ("install the NVIDIA driver and the NVIDIA container toolkit, then `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml` "
        "(https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)")

Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)


def enabled(sim: dict) -> bool:
    """The part is on in this simulation (`workbench.effective` sets `real_gpu.enabled`), so the node exists."""
    return bool((sim.get("real_gpu") or {}).get("enabled"))


def gres_type(model: str) -> str:
    """A Slurm GRES type from the card's model: letters, digits and hyphens (\"NVIDIA GeForce RTX 3050 Laptop GPU\" -> geforce-rtx-3050-laptop-gpu)."""
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower().replace("nvidia", "")).strip("-")
    return slug or "real"


def cards(runner: Runner = _run) -> list[str]:
    """The NVIDIA cards the host driver reports (`nvidia-smi -L`), or none when there is no driver or no tool."""
    try:
        r = runner(["nvidia-smi", "-L"])
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.split(" (UUID", 1)[0] for ln in r.stdout.splitlines() if r.returncode == 0 and ln.startswith("GPU ")]


def cdi_ready(specs: tuple[Path, ...] = CDI_SPECS) -> bool:
    return any(p.is_file() for p in specs)


def problems(runner: Runner = _run, specs: tuple[Path, ...] = CDI_SPECS) -> list[str]:
    """Why the part cannot start here (one message per condition), empty when it can."""
    if not cards(runner):
        return [f"the real-gpu part needs an NVIDIA card with its driver on this machine (`nvidia-smi -L` lists none): {HINT}"]
    if not cdi_ready(specs):
        return [f"the NVIDIA container toolkit has no CDI spec yet (looked for {' and '.join(map(str, specs))}): {HINT}"]
    return []


def wire(services: dict) -> None:
    """The device, the Hostname label (an environment variable, not the container hostname: Docker resolves a hostname to the container, so slurmctld reached this one instead of the node) and the published port; no mock profile is set, so start.sh runs the exporter on the real driver."""
    svc = services.get(SERVICE)
    if svc is None:
        return
    svc.update({"environment": {"DCGM_HOSTNAME": HOSTNAME}, "devices": [DEVICE], "command": ["-c", "3000"], "ports": [f"127.0.0.1:{PORT}:9400"]})
