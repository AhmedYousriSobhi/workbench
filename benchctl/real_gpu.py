"""The real GPU, opt-in (the `real-gpu` part, issue 428, SPEC C-23): the host's own NVIDIA card with the real dcgm-exporter.

One container gets the card as a CDI device (`nvidia.com/gpu=all`, the NVIDIA container toolkit): default capabilities, no
`privileged`, no `cap_add`. The same image as the mock exporter (`components/dcgm-exporter`) runs with no mock profile, so it reads the real
driver: real temperature, power, utilisation and framebuffer, the real model name. It sits next to the simulated GPUs, never in place of
them. Refused with one message when the machine has no NVIDIA driver or no CDI spec."""
import subprocess
from collections.abc import Callable
from pathlib import Path

SERVICE = "real-gpu"
PORT = 9403                    # published on 127.0.0.1 (the mock exporter 9402, the farm 9400)
LIMIT_MB = 640                 # measured 481 MB with the real driver: the host engine alone takes 404 MB (125 MB in total with the mock)
HOSTNAME = "gpu-real"          # DCGM's Hostname label, so a client tells it from gpu001, gpu002
DEVICE = "nvidia.com/gpu=all"  # CDI name, from /var/run/cdi/nvidia.yaml (`nvidia-ctk cdi generate`)
CDI_SPECS = (Path("/var/run/cdi/nvidia.yaml"), Path("/etc/cdi/nvidia.yaml"))
HINT = ("install the NVIDIA driver and the NVIDIA container toolkit, then `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml` "
        "(https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)")

Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)


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
    """The device, the hostname and the published port; no mock profile is set, so start.sh runs the exporter on the real driver."""
    svc = services.get(SERVICE)
    if svc is None:
        return
    svc.update({"hostname": HOSTNAME, "devices": [DEVICE], "command": ["-c", "3000"], "ports": [f"127.0.0.1:{PORT}:9400"]})
