# 18. Network trouble inside the cluster

**You will:** add delay and packet loss to one node's network and see how Slurm reacts. About 5 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).
- The root governor service, installed once: `sudo ./scripts/install-governor.sh`. Nothing else in workbench needs `sudo`.

## Steps

1. Add 200 ms of delay and 5 % loss to `cn001` for 60 seconds. It prints `cn001: netem delay 200ms loss 5%, ends in 60s`.

   ```bash
   ./scripts/benchctl set netem cn001 --delay 200ms --loss 5% --ttl 60s
   ```

2. Measure it from `slurmctld` and from the login node. The loss is random: with 4 packets you may see 0 to 25 %. Expect about 200 ms round trip on the controller and 200 to 400 ms from the login node.

   ```bash
   ./scripts/benchctl exec slurmctld -- ping -c 4 -W 2 cn001
   ./scripts/benchctl exec login -- ping -c 4 -W 2 cn001
   ```

   In a 30 s test `benchctl logs slurmctld` showed no slow-reply line and the node stayed `IDLE`: 200 ms is slower than normal but well inside Slurm's timeouts.

3. Clear it early. It prints `netem cleared on cn001`, and the ping is back to under 1 ms.

   ```bash
   ./scripts/benchctl clear netem cn001
   ```

4. Or play the same fault as a scenario. It takes 90 s (the rule ends by itself at 60 s, and the scenario clears again at 90 s).

   ```bash
   ./scripts/benchctl scenario run netem-slow-link
   ```

## You should see

- Replies from `cn001` take about 200 ms and some are lost. At these values the node does not flap.
- After the time limit, or `clear netem`, every rule is gone. The governor also removes every rule when it starts and when it exits.

## If it does not work

- `netem needs the root governor service`: only the rootless governor runs. Install the service (above).
- `paused: memory pressure`: free memory first.

## Next

- [Network faults](../guides/network-faults.md): the safety rules and the limits.
