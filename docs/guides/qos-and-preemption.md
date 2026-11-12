# QOS, priority and preemption

workbench can run real Slurm QOS objects, multifactor priority and QOS preemption (issue 544, ADR 0055), so a client sees the pending reasons and requeues of a production cluster.

## Turn it on

Needs job accounting (slurmdbd). In `sim.yaml`:

```yaml
qos:
  - {name: scavenger, priority: 1, max_gpus_per_user: 2}
  - {name: urgent, priority: 1000, preempt: [scavenger], grace_time_s: 30}
slurm:
  priority: {weight_qos: 1000, weight_age: 100, weight_fairshare: 10000, decay_half_life: 7-0}
```

```bash
./scripts/benchctl up --profile std      # std has the accounting part; or: benchctl up --with accounting
```

Without the accounting part `up` stops with one message (`qos: and slurm.priority: need job accounting`). A changed `sim.yaml` needs `benchctl up` again; the QOS are set in slurmdbd by the `accounting-init` container.

## What you see

```bash
docker exec workbench-slurmctld-1 sacctmgr show qos format=name,priority,maxtrespu,preempt,gracetime
docker exec workbench-slurmctld-1 squeue -o '%i %T %r'       # QOSMaxGRESPerUser, Priority, Resources, BeginTime
docker exec workbench-slurmctld-1 sacct -X -o JobID,QOS,State    # PREEMPTED / REQUEUED
```

A job over `max_gpus_per_user` waits with `QOSMaxGRESPerUser`. A job under a QOS that lists `preempt` requeues running jobs of the listed QOS when it needs their resources (after `grace_time_s`).

## Submit under a QOS

`benchctl submit` and the scenario step take `qos:`:

```yaml
- {at: 0s, target: slurm, do: {submit: {node: gpu001, user: bob, gpus: 8, duration: 600, qos: scavenger}}}
```

Scenario `preempt-burst` (SC-37) does exactly this, then an `urgent` job for the same GPUs at 20 s; the scavenger job is requeued and runs again after the urgent job ends.

## Not exercised

The monitoring client against these states; fairshare with several accounts; other preempt modes.
