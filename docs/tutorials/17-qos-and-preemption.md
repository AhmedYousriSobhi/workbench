# 17. QOS and preemption

**You will:** see real Slurm QOS limits, and watch an urgent job requeue a scavenger job. About 10 minutes.

## You need

- The `std` profile, which has job accounting.

- The `qos:` and `slurm:` keys of `sim.yaml` switched on. They are commented out by default, and the scenario needs them. Remove the leading `# ` from these lines:

  ```yaml
  qos:
    - {name: scavenger, priority: 1, max_gpus_per_user: 2}
    - {name: urgent, priority: 1000, preempt: [scavenger], grace_time_s: 30}
  slurm:
    priority: {weight_qos: 1000, weight_age: 100, weight_fairshare: 10000, decay_half_life: 7-0}
  ```

  Undo it later with `git checkout sim.yaml`; do not commit it.

## Steps

1. Start it. Run it again after every change to `sim.yaml` (it takes a few seconds on a running cluster).

   ```bash
   ./scripts/benchctl up --profile std
   ```
2. List the QOS. You get `normal`, `scavenger` (`gres/gpu=2`) and `urgent` (preempts `scavenger`, grace time `00:00:30`).

   ```bash
   docker exec workbench-slurmctld-1 sacctmgr show qos format=name,priority,maxtrespu,preempt,gracetime
   ```

3. Start a 2-GPU scavenger job on `gpu001` (the QOS limit). At 20 seconds an urgent job asks for all 8 GPUs of the node. It takes 150 s; run steps 4 and 5 in a second terminal (the scavenger job runs at once, `squeue` shows the urgent job `RUNNING` and the scavenger job `PENDING BeginTime` after about 40 s).

   ```bash
   ./scripts/benchctl scenario run preempt-burst
   ```

4. Read the pending reasons while it runs.

   ```bash
   docker exec workbench-slurmctld-1 squeue -o '%i %T %r'
   ```

5. Read the history afterwards.

   ```bash
   docker exec workbench-slurmctld-1 sacct -X -o JobID,QOS,State
   ```

## You should see

- `sacct` shows the scavenger job `PREEMPTED` and the urgent job `COMPLETED`. The scavenger job is requeued (`PENDING`, reason `BeginTime`); the scenario cancels it at 150 s, so it may not run again before then (not seen here).
- Pending jobs show reasons such as `BeginTime` (after a requeue) or `QOSMaxGRESPerUser` (a job over its QOS limit: submit a scavenger job with more than 2 GPUs).

## Change the QOS

- Edit the `qos:` and `slurm.priority:` keys of `sim.yaml`, then run `./scripts/benchctl up` again.
- Without the accounting part, `up` stops with one message.

## Next

- [QOS, priority and preemption](../guides/qos-and-preemption.md): the keys, and a job submitted under a QOS.
