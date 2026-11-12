# ADR 0051: One generated node inventory, and one GPU node shape

Status: accepted (owner decision of 2026-11-12 on the shape). Issue 462; epic 474, config lab epic 557.

## Context

SPEC section 3 says every artifact derives from `sim.yaml` so node names and shapes agree. They did not: the BMC farm, the BCM mock and the GPU exporter each parsed `sim.yaml` on their own, and a GPU node had 8 CPUs and 4096 MB in Slurm but 64 CPUs and 16384 MB in Redfish and BCM. The topology, DRA and typed-GPU work of the config lab needs one place that says what a node is.

## Decision

- `benchctl/hwinventory.py` builds one `inventory.json` from the effective `sim.yaml` (the copy `compose.write` already writes, with the scaled compute count): for each machine its name, kind (`compute`, `gpu`, `k8s-cpu`, `k8s-gpu`), CPUs, memory in MB, GPU count and type, BMC serial and GPU UUIDs, in the order the BMC farm numbers them, plus `gpu_type` and `bcm_manages_kubernetes`. It is written to `compose/generated/inventory.json` (never hand-edited) and mounted read-only into `bmc-farm`, `bcm-mock` and `gpu-exporter-farm`.
- The mocks read only that file; none parses `sim.yaml`. The BMC and BCM images no longer need `pyyaml`. Slurm's `slurm.conf` takes the GPU node shape from `hwinventory.gpu_shape`, and `observability/gen_targets.py` takes the names from the same build.
- **GPU node shape (owner decision):** `sim.yaml` `gpu.cpus` and `gpu.memory_mb`, default 8 CPUs and 2048 MB per GPU. For the shipped `sim.yaml` that is 64 CPUs and 16384 MB for 8 GPUs, now the same in Slurm, Redfish and BCM. Slurm's GPU nodes change from 8 CPUs and 4096 MB; the memory is a declared number (`RealMemory`), not real RAM.
- `tests/test_inventory_shapes.py` compares Slurm, Redfish, BCM and the exporter node by node, and checks that serials and GPU UUIDs in the inventory are what the mocks publish. Unit tests of the mocks get an inventory from `tests/invhelp.py`.

## Consequences

A new node attribute (topology leaf, MIG shape, DRA device) is added once, in `hwinventory.py`, and read by whoever needs it. A running cluster made before this needs `benchctl up` to write the file and mount it (the mounts changed). The real card's node (`real_gpu`) is still not in the inventory (issue 431). Serials and GPU UUIDs are still formed by the mocks from the same formula and checked against the inventory by a test; moving them to be read from the file is a later step.
