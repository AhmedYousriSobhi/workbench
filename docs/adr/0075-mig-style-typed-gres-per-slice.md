# ADR 0075: MIG-style typed GRES per slice, off by default

Status: accepted by the owner on 2026-11-14 (issue 546, epic 557). Built; the live run is pending.

## Context
Production GPU nodes often run MIG, where Slurm sees typed GRES such as `gpu:1g.18gb:56` and jobs ask for them with `--gres=gpu:<type>:N`. The simulator only had count-based, untyped GPUs, so a config lab or a client could not meet that shape.

## Decision
- An optional `sim.yaml` section `gpu.mig: {nodes: [gpu002], profile: 1g, slices: 7}` (off by default, validated with one message per mistake, `benchctl/mig.py`). Every GPU of a listed GPU node is split into `slices` units (1 to 7). The type name is `<gpu type>_<profile>` (`h200_1g`).
- **Owner choice (2026-11-14): Slurm and the inventory only.** A listed node advertises `gpu:h200_1g:<gpus_per_node x slices>` (8 x 7 = 56) in its own `NodeName` and `gres.conf` lines, backed by one stand-in device file per slice (`WORKBENCH_GRES_DEVICES`). Other GPU nodes keep plain `gpu:h200:8`. `inventory.json` carries `gpu_mig` for the node.
- **Metrics and the health check stay per physical GPU.** The exporter publishes one series per physical GPU and the node health check expects the physical count (`WORKBENCH_GPUS`), so neither changes. The GPU allocation feed maps a slice index to its physical GPU (`index // slices`) so a job holding slices makes the right GPUs read busy.
- Per-slice metrics (GPU instance labels, DCGM overrides, a slice-count health check) are not built; they are a separate decision if a client needs them.

## Not verified yet
`./scripts/verify.sh --unit mig`: that jobs can request the typed GRES and land on the MIG node, that `scontrol show node` shows the typed count and usage, that the exporter shows the right GPUs busy, and that the node health check stays quiet.

## Consequences
- One more section in `sim.yaml`, ignored unless present; nothing changes for existing clusters.
- A MIG node gets up to 56 stand-in symlinks in `/dev`, no capability or memory cost worth declaring.
- Untyped requests (`--gres=gpu:2`) still work on every node; a typed request only runs where that type exists.
