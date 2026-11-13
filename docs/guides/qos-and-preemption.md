<p align="center"><img src="../assets/art/guide-qos-and-preemption-hero.svg" alt="An urgent job arrow pushing a scavenger job back to the queue" width="720"></p>

# QOS, priority and preemption

**In 30 seconds:** declare QOS levels in `sim.yaml`, and real Slurm shows the pending reasons and requeues of a production cluster. It needs job accounting.

## Do it

```bash
./scripts/benchctl up --profile std      # std has accounting
./scripts/benchctl scenario run preempt-burst
```

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

## Under the hood

<p align="center"><img src="../assets/art/guide-qos-and-preemption-diagram.svg" alt="A timeline: the scavenger job runs, an urgent job arrives, the scavenger job is requeued, then runs again" width="720"></p>

> [!HOOD]
> `benchctl/qos.py` validates `qos:` and `slurm.priority:` and writes both outputs: the `slurm.conf` lines (`PriorityType=priority/multifactor`, the weights, `PreemptType=preempt/qos` with `PreemptMode=REQUEUE` when any QOS lists `preempt`) and the `sacctmgr` commands that `accounting-init` runs in slurmdbd. `max_gpus_per_user` becomes `MaxTRESPerUser=gres/gpu=N`.
> Found live: `AccountingStorageEnforce` defaults to none, so `limits,qos` is set; slurmdbd refuses a GPU limit until slurmctld has registered the TRES, so `accounting.sh` waits up to a minute.

Source: `benchctl/qos.py`, [ADR 0055](../adr/0055-real-qos-priority-and-preemption-from-sim-yaml.md)

## Tips

> [!TIP]
> `sacct -X -o JobID,QOS,State` shows PREEMPTED and REQUEUED after the burst.

## If it goes wrong

| Symptom | Try |
|---|---|
| `up` says `qos: and slurm.priority: need job accounting` | add the accounting part: `benchctl up --with accounting` |
| A change is not live | run `benchctl up` again; the QOS are set by the `accounting-init` container |

## Learn more

- [Scenarios](../manual/06-scenarios.md)
- [Slurm resilience](slurm-resilience.md)
