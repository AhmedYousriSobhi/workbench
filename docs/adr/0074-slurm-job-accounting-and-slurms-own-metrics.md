# ADR 0074: Job usage from /proc and Slurm's own metrics endpoint

Status: accepted by the owner on 2026-11-14 (issue 459, epic 557). Built; the live runs are pending (see below).

## Context
`JobAcctGatherType=jobacct_gather/none` gave `sstat` and `sacct` no CPU or memory usage, and Prometheus had no Slurm metrics at all, so the lab's GPU-seconds and QOS metrics, a drained-node alert and any Slurm dashboard had nothing to read.

## Decision
- **`jobacct_gather/linux`** with `JobAcctGatherFrequency=task=10`: usage is read from `/proc`, which the unprivileged containers can do (the synthetic jobs use `proctrack/linuxproc` and `task/none` as before; no cgroup write access is needed). Ten seconds instead of the default 30 so a short job shows some usage.
- **Slurm's own endpoint, not a third-party exporter.** The pinned build (26.05.5, ADR 0002) ships `metrics_openmetrics.so`; `MetricsType=metrics/openmetrics` makes `slurmctld` answer `/metrics/jobs`, `/metrics/nodes`, `/metrics/partitions` and `/metrics/scheduler` on its own port 6817. No new container or process, nothing to install. Checked in the image: the plugin and the endpoint names are present. `/metrics/jobs-users-accts` is not scraped: a series per user and account is the cardinality this workbench caps.
- Prometheus scrapes the four paths as jobs `slurm-jobs`, `slurm-nodes`, `slurm-partitions` and `slurm-scheduler`, generated with the other part jobs (ADR 0073). The dashboard `slurm-overview` (nodes by state, jobs, GPUs and CPUs allocated, pending per partition, drained and down nodes) is provisioned beside the GPU overview; it sets no threshold colours.
- The `NodeDrained` rule (ADR 0072 addendum) reads `slurm_nodes_drain`.

## Not verified yet
`./scripts/verify.sh --unit slurm_metrics`: that a running job shows non-zero usage in `sstat` inside the unprivileged node, that the endpoint answers Prometheus without a token, and that a drained node raises `NodeDrained`. The metric families were read from the plugin's strings, not from a live scrape; the per-partition and scheduler panels use labels that the live run confirms.

## Consequences
- Memory: no new process. The scrape adds four small requests every 15 s to `slurmctld`; the gather plugin adds one `/proc` walk per task every 10 s. The measured cost goes into `docs/guides/memory.md` after the live run.
- `slurm.conf` changes, so a running cluster needs `benchctl up` (a restart of the Slurm daemons) to pick it up.
