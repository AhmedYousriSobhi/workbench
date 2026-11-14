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

## Addendum (issue 674): the nodes hold `SYS_PTRACE`
The live run showed that the first Decision bullet is wrong for jobs of the simulated users: `jobacct_gather/linux` reads `/proc/PID/io` of each job process, and slurmd (root inside an unprivileged node container, no `CAP_SYS_PTRACE`) gets "Permission denied" for a process another user owns. `sstat` and `sacct` then show no memory for alice's job and a normal figure for a root job. A throwaway container confirmed it both ways: the read is denied without the capability and succeeds with `--cap-add SYS_PTRACE`.

**Decision (owner, 2026-11-14):** the simulation must show user jobs' memory the way a real cluster does, so the Slurm node containers (`workbench.node`) get `cap_add: [SYS_PTRACE]`. This is the only capability and the only exception to "containers stay unprivileged": the watchdog (`governor/watchdog.py`) and `benchctl doctor` accept `SYS_PTRACE` on a container labelled `workbench.node`, list it as an accepted exception, and report any other capability, or this one on any other container, as a violation. Options not taken: keeping the gap and testing with a root job (the simulation would differ from a real cluster for exactly the users a client watches), and switching the plugin off (root jobs would lose usage too).

What it allows: processes with the capability in that container may inspect other processes in the same container (the job processes are the only ones that matter); it grants no access to the host or to other containers. Non-root users in the node (ssh sessions, jobs) do not hold it.

Not verified yet: `./scripts/verify.sh --unit slurm_metrics --clean` with alice's job (the existing test already submits as alice).
