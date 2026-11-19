# 14. Reboot and power-cycle nodes

**You will:** power a node down and up with Slurm's own commands, and watch the fake BMC follow. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).

## Steps

1. Power a node down through Slurm.

   ```bash
   docker exec workbench-slurmctld-1 scontrol power down cn002
   ```

2. Read the state. Wait a few seconds: `sinfo` shows `idle%` (powering down) and then `idle~` (powered down). `scontrol` spells the flags out.

   ```bash
   docker exec workbench-slurmctld-1 sinfo -n cn002
   docker exec workbench-slurmctld-1 scontrol show node cn002
   ```

   `scontrol` shows `State=IDLE+POWERING_DOWN`, then `State=IDLE+POWERED_DOWN`. A `power up` sent while it is still powering down has no effect: wait for `idle~` first.

3. Power it up. The node is `State=IDLE` again within a few seconds.

   ```bash
   docker exec workbench-slurmctld-1 scontrol power up cn002
   ```

4. Do a reboot as soon as the node's jobs end, as an admin does for a kernel update. It takes 90 s: `cn002` reboots at 0 s, `cn003` is powered down at 40 s and up at 90 s.

   ```bash
   ./scripts/benchctl scenario run reboot-asap
   ```

5. Cut the power the way a failed PSU would. Each prints `cn001: off` or `cn001: on`. In between, run `benchctl describe node cn001`: the container is `paused` and the BMC says `power Off`.

   ```bash
   ./scripts/benchctl set node cn001 off
   ./scripts/benchctl set node cn001 on
   ```

## You should see

- `sinfo` shows `idle%`, then `idle~`, then `idle` again after `power up`.
- The BMC reports `PowerState` `Off`, and the Redfish log shows `Reset ForceOff` then `Reset On` for the reboot.
- After `set node cn001 off`, Slurm shows `IDLE+NOT_RESPONDING` after about 20 seconds and `DOWN+NOT_RESPONDING` after about 30; after `on` it is `IDLE` again within a minute.
- Not exercised: the Redfish log lines `Reset ForceOff` and `Reset On` of the reboot.

## Next

- [Power and reboot](../guides/power-and-reboot.md): the states and the BMC wiring.
- [Break a node](03-break-a-node.md).
