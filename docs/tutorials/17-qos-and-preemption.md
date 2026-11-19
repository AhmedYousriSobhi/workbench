# 17. QOS and preemption

**You will:** see real Slurm QOS limits, and watch an urgent job requeue a scavenger job. About 10 minutes.

## You need

- The `std` profile, which has job accounting.

## Steps

1. Start it.

   ```bash
   ./scripts/benchctl up --profile std
   ```

2. List the QOS.

   ```bash
   docker exec workbench-slurmctld-1 sacctmgr show qos format=name,priority,maxtrespu,preempt,gracetime
   ```

3. Fill every GPU of `gpu001` with a scavenger job. At 20 seconds an urgent job asks for the same GPUs.

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

- The scavenger job goes `PREEMPTED`, then `REQUEUED`, and runs again after the urgent one ends.
- Pending jobs show reasons such as `QOSMaxGRESPerUser`, `Priority` or `Resources`.

## Change the QOS

- Edit the `qos:` and `slurm.priority:` keys of `sim.yaml`, then run `./scripts/benchctl up` again.
- Without the accounting part, `up` stops with one message.

## Next

- [QOS, priority and preemption](../guides/qos-and-preemption.md): the keys, and a job submitted under a QOS.
