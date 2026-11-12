# ADR 0055: Real QOS, multifactor priority and preemption from sim.yaml

Status: accepted. Issue 544; epic 557 (config lab A-6 and A-7). Ticks off the "QOS in sim.yaml" gap of SPEC section 3 (issue 471).

## Decision

- Optional `qos:` in `sim.yaml`: a list of `{name, priority, max_gpus_per_user, grace_time_s, preempt: [names]}`, and optional `slurm.priority:` (`weight_qos|age|fairshare|job_size|partition`, `decay_half_life`). No key, no change.
- `benchctl/qos.py` is the one place that validates them and writes both outputs: slurm.conf lines (`PriorityType=priority/multifactor`, the weights, `AccountingStorageTRES=gres/gpu` when a GPU limit is set, and `PreemptType=preempt/qos` with `PreemptMode=REQUEUE` when any QOS lists `preempt`) and the idempotent `sacctmgr` lines of `accounting.sh` (add, then `modify qos where name=X set ...`, so a changed sim.yaml reaches a database that already has the QOS). Every simulated user may submit under every declared QOS; their own `qos:` stays the default.
- QOS live in slurmdbd, so `compose.generate` refuses `qos:` or `slurm.priority:` without the accounting part, with one message that says how to add it.
- The scenario `submit` step and `benchctl submit` take `qos:`. Scenario `preempt-burst` (SC-37) fills the GPUs with a scavenger job and asks for them under `urgent`.

## Found live

`AccountingStorageEnforce` defaults to none: Slurm then records QOS limits but enforces none, so `limits,qos` is set (a user then needs an association, which `accounting.sh` makes for the simulated users).

slurmdbd answers `no TRES known by type gres/gpu` to a GPU limit until slurmctld has registered the TRES, which needs `AccountingStorageTRES=gres/gpu` in slurm.conf; `accounting.sh` waits for the TRES (up to a minute) before it sets a limit.

## Consequences

The pending reasons and the requeue are Slurm's own (`QOSMaxGRESPerUser`, `BeginTime` after a requeue, `sacct` state). Not covered: fairshare trees and accounts other than `sim`, `PreemptMode=SUSPEND/CANCEL`, per-QOS `PreemptMode`, QOS limits other than GPUs per user.
