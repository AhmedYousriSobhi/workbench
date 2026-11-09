# ADR 0037: The host's own NVIDIA card, opt-in

Status: accepted (owner, 2026-11-09). Issue 428. SPEC C-23.

## Context
SPEC section 1 lists real GPUs as a non-goal, and every GPU in the workbench is simulated (the farm, ADR 0005; the real DCGM on a mock NVML, ADR 0025). The owner's machine has a real NVIDIA card, and a client tested against real DCGM output from real hardware, next to the simulated GPUs, is worth having.

## Spike (2026-11-09, RTX 3050 laptop card, driver 580)
- An unprivileged container with the CDI device `nvidia.com/gpu=all` (default capabilities) runs the real `nvidia-smi`. Compose passes it as `devices: [nvidia.com/gpu=all]`.
- The official dcgm-exporter in that container reports the real model name, temperature, power, utilisation and framebuffer.
- With the real driver the host engine alone takes 404 MB (the mock needs 25 MB); the exporter 77 MB. A 256 MB limit got the host engine OOM-killed (exit 137), so the part's limit is 640 MB (measured 481 MB).
- The host engine dies on the CPU module under a non-NVIDIA CPU only when it is daemonised; the image runs it in the foreground with module 9 (SysMon) denied.

## Decision
An opt-in part `real-gpu`: one container, the same image as the mock exporter (`components/dcgm-exporter`) run with no mock profile, the card passed as a CDI device, 127.0.0.1:9403, `Hostname` gpu-real. Refused with one message when there is no card, driver or CDI spec (`benchctl/real_gpu.py`). Off by default and by name only; no profile includes it. Containers stay unprivileged: a device is not a capability, and `doctor` still checks `privileged` and `cap_add`.

## Not decided or not built
- Slice 2, a Slurm node on the real card so jobs can use it: needs its own gres line, node kind and a decision on how the BMC and BCM mocks describe it.
- The simulated nodes still carry no `nvidia-smi` or `dcgmi`; only the exporter containers do.
- Other machines: the part skips itself everywhere there is no NVIDIA card.
