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
The node is a mode of the part, not a part of its own: `--with real-gpu` adds `rgpu001` (kind `real-gpu`, IP 10.141.254.1) built from `node-base` with the same CDI device, and the exporter's `Hostname` label is `rgpu001` (set by `DCGM_HOSTNAME`, never as the container's hostname: Docker's DNS would then answer `rgpu001` with the exporter as well as the node and slurmctld marks the node down, issue 435), so the node and its exporter are one machine to a client. `slurm.conf` gives it its own `NodeName` (`Gres=gpu:<model slug>:1`) and its own partition `real-gpu`, so a simulated job cannot land on the card (and a job for it must name the partition, `srun -p real-gpu`: with the default `cpu` partition `--nodelist rgpu001` is refused as "Requested node configuration is not available"); `gres.conf` points at `/dev/nvidia0`. `WORKBENCH_GPUS` stays 0 on it (no stand-in device files, and the simulated-GPU health checks skip it). It sits with the GPU nodes on the page, whose state comes from the card's exporter. The BMC farm and BCM mock list it (see the issue 431 addendum); the exporter farm does not; jobs run `nvidia-smi` and shell or Python (a CUDA runtime is issue 430).

## Not decided or not built
- CUDA: issue 430.
- Nodes carry the `dcgmi` client (issue 443, copied from the exporter image into `node-base`, 5 MB) but no `nvidia-smi` and no host engine. A node with an exporter (`rgpu001`, and the GPU nodes of the dcgm part) has `WORKBENCH_DCGM_HOST` pointing at its exporter's host engine, which listens on the cluster network (`DCGM_ENGINE_BIND=ALL`, internal only, nothing published), so `dcgmi discovery -l` on `rgpu001` lists the real card; on any other node `dcgmi` says it cannot reach a host engine, as on a real machine with none running.
- Other machines: the part skips itself everywhere there is no NVIDIA card.

## Addendum: BMC and BCM list the real node (issue 431)
With the BMC farm and BCM mock on, `rgpu001` is a machine in the one generated inventory (ADR 0051): kind `real-gpu`, 1 GPU, listed after every simulated node, so no simulated node changes its place, address or BMC port. The open questions of the issue, resolved from the code:
- **Model name.** `workbench.effective` already asks the host once (`real_gpu.cards()`, `nvidia-smi -L`) and stores it in `sim["real_gpu"]`; `hwinventory.build` copies it into the node's `gpu_model` (and the Slurm type slug into `gpu_type`). The mocks read `inventory.json` and never run `nvidia-smi`, so unit tests stay hermetic (they stub `real_gpu.cards`). With no reading the model is `real`.
- **BMC group.** `slurm-gpu` (`nodes.KIND_GROUP["real-gpu"]`): a selection that drops that group or lists `bmc.nodes` without `rgpu001` leaves it without a BMC, as for any GPU node. Its BMC address is 10.148.254.1 (third octet 254, the ipmi twin of 10.141.254.1; `addressing.BLOCK` and `ROOM`). The Redfish system shows the model as `Oem.GpuModel` next to `Oem.GpuCount` (1); a simulated node's system has no `GpuModel` and is unchanged.
- **CPU and memory.** Not the 8 CPUs and 2048 MB per GPU of the simulated GPU nodes: `hwinventory.real_gpu_shape` gives the node the compute shape (`compute.cpus`, `compute.memory_mb`; the node is a plain `node-base` container) unless `real_gpu.cpus` / `real_gpu.memory_mb` is set in `sim.yaml`. `slurm.conf`, Redfish and BCM all read it, so the three agree (the rule of issue 462).
- **BCM.** Category `gpu` (the inventory's `category`; the node's kind stays `real-gpu` for everything else) and the node's own address 10.141.254.1 (the inventory's `ip`), so BCM, Slurm and ssh name one address; the simulated gpu nodes keep theirs.
- **Exporter farm.** Not listed, and no `sim-nodes` Prometheus target: the card's own exporter and the `real-gpu` scrape job already serve it. The page's Clear faults sends it to the BMC farm only.
Not exercised live: the unit tests cover the lists and inputs; a live check (`benchctl get nodes`, `/redfish/v1/Systems/1` through `bmc-rgpu001`, the BCM device list) is for the owner to run on a machine with a card.
