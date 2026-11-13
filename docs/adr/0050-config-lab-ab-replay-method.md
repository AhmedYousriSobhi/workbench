# ADR 0050: Config lab: A/B replay, comparison and verdict

Status: accepted (owner decisions of 2026-11-12). Issue 542; epic 557. Companion to ADR 0049.

## Decision

- **Shape-preserving downscale.** `lab.max_nodes` (default 24, max 64). Every (partition x node shape x topology leaf) group keeps at least one node; the rest is split in proportion to production counts. Partitions, QOS, accounts and topology levels are all kept. Multiple-slurmd mode stays out of v1 (issue 457).
- **Trace** is a `sacct` export (fields in the operator guide), sampled at the node ratio, stratified by account, QOS and partition. Each job is resubmitted with the mapped user, account, QOS, partition, TRES (typed GPUs included), nodes, time limit and relative submit time; its body is `sleep <scaled elapsed>; exit <code>` (timeouts sleep past the limit, OOM exits 137).
- **Names.** Production node names are kept as Slurm `NodeName` and hostname (compose service `lab-node-NNN`). Users map to `lab-u001...`, `--keep-users` keeps them.
- **Time dilation.** Default target 2 h per side (30 min to 6 h). Every time-based scheduling parameter (see `KEY_CLASS` time-scaled) is divided by the same factor in both runs and listed in the report.
- **Same faults, same seed.** An attached scenario runs with a fixed seed at the same dilated times in both runs.
- **Snapshot and restore.** Before run A save `StateSaveLocation` and a `mysqldump`; restore both before run B. Prometheus data carries a `lab_run` label instead of being restored.
- **Repeats.** One run per side by default; `--repeat N` (at most 5) reports median and range; single runs are labelled.
- **Concurrency cap** `lab.max_running_jobs` (default 50). If the sampled trace exceeds it the import proposes fewer lab nodes rather than throttling submissions, so both runs see the same load.
- **Pressure.** Replay and validation one-shots are class P5 and admitted only if the estimate (250 MB) fits (GV-9). L2 pauses the replay clock (pauses are recorded; over 5 % of run length marks the run not comparable); L3 stops it as "aborted under memory pressure".
- **Verdict** (thresholds in `sim.yaml lab.verdict`, printed in every report): any hard failure (validation error, lost job, an alert that fired in A and not in B, still-pending up more than 20 %, median wait up more than 20 % in a partition with enough samples) is "Do not ship"; a new cost or a 5 to 20 % regression is "Ship with changes"; at least one improvement over 5 % and nothing worse is "Ship"; everything within 5 % is "No effect". A minimum sample size per partition keeps small partitions from causing false failures.
- **Suggestions** are rule-based from a small catalogue (preemptions with requeue, QOS limit dominating pending, idle-but-free GPUs, alert flapping, fairshare spread), never generated; "apply suggestion and rerun" edits the candidate overlay and starts a new A/B.
- **Report:** `report.md` and `report.json` under `compose/generated/lab/NAME/reports/RUN/`; `benchctl export report --anonymize` maps production node names to lab names.

## Consequences

Policy behaviour (priority, fairshare, QOS, preemption, topology placement, Kubernetes admission and quotas, alert rule timing, Slurm state compatibility) is decided by the real software. Job runtimes, GPU and network contention, hardware timing, pod runtime and slurmctld load at production scale are approximate and labelled so in every report. A ratio above 100x marks results indicative.
