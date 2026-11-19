# 18. Network trouble inside the cluster

**You will:** add delay and packet loss to one node's network and see how Slurm reacts. About 5 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).
- The root governor service, installed once: `sudo ./scripts/install-governor.sh`. Nothing else in workbench needs `sudo`.

## Steps

1. Add 200 ms of delay and 5 % loss to `cn001` for 60 seconds.

   ```bash
   ./scripts/benchctl set netem cn001 --delay 200ms --loss 5% --ttl 60s
   ```

2. Watch `slurmctld` see slow or lost replies.

   ```bash
   ./scripts/benchctl logs slurmctld
   ```

3. Clear it early.

   ```bash
   ./scripts/benchctl clear netem cn001
   ```

4. Or play the same fault as a scenario.

   ```bash
   ./scripts/benchctl scenario run netem-slow-link
   ```

## You should see

- Replies from `cn001` are slow or missing, and the node may flap.
- After the time limit, or `clear netem`, every rule is gone. The governor also removes every rule when it starts and when it exits.

## If it does not work

- `netem needs the root governor service`: only the rootless governor runs. Install the service (above).
- `paused: memory pressure`: free memory first.

## Next

- [Network faults](../guides/network-faults.md): the safety rules and the limits.
