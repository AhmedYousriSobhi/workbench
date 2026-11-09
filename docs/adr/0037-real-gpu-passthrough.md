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
An opt-in part `real-gpu`: one container, the same image as the mock exporter (`components/dcgm-exporter`) run with no mock profile, the card passed as a CDI device, 127.0.0.1:9403, `Hostname` rgpu001. Refused with one message when there is no card, driver or CDI spec (`benchctl/real_gpu.py`). Off by default and by name only; no profile includes it. Containers stay unprivileged: a device is not a capability, and `doctor` still checks `privileged` and `cap_add`.

## Slice 2: the Slurm node (owner decisions, 2026-11-09)
The node is a mode of the part, not a part of its own: `--with real-gpu` adds `rgpu001` (kind `real-gpu`, IP 10.141.254.1) built from `node-base` with the same CDI device, and the exporter's `Hostname` label is `rgpu001` (set by `DCGM_HOSTNAME`, never as the container's hostname: Docker's DNS would then answer `rgpu001` with the exporter as well as the node and slurmctld marks the node down, issue 435), so the node and its exporter are one machine to a client. `slurm.conf` gives it its own `NodeName` (`Gres=gpu:<model slug>:1`) and its own partition `real-gpu`, so a simulated job cannot land on the card (and a job for it must name the partition, `srun -p real-gpu`: with the default `cpu` partition `--nodelist rgpu001` is refused as "Requested node configuration is not available"); `gres.conf` points at `/dev/nvidia0`. `WORKBENCH_GPUS` stays 0 on it (no stand-in device files, and the simulated-GPU health checks skip it). It sits with the GPU nodes on the page, whose state comes from the card's exporter. The BMC farm, BCM mock and exporter farm do not list it (issue 431); jobs run `nvidia-smi` and shell or Python (a CUDA runtime is issue 430).

## Not decided or not built
- The BMC and BCM listing: issue 431. CUDA: issue 430.
- The simulated nodes still carry no `nvidia-smi` or `dcgmi`; only the exporter containers do.
- Other machines: the part skips itself everywhere there is no NVIDIA card.
