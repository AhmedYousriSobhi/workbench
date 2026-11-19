# 14. Reboot and power-cycle nodes

**You will:** power a node down and up with Slurm's own commands, and watch the fake BMC follow. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).

## Steps

1. Power a node down through Slurm.

   ```bash
   docker exec workbench-slurmctld-1 scontrol power down cn002
   ```

2. Read the state. It shows `POWERED_DOWN`.

   ```bash
   docker exec workbench-slurmctld-1 sinfo -n cn002
   ```

3. Power it up.

   ```bash
   docker exec workbench-slurmctld-1 scontrol power up cn002
   ```

4. Do a reboot as soon as the node's jobs end, as an admin does for a kernel update.

   ```bash
   ./scripts/benchctl scenario run reboot-asap
   ```

5. Cut the power the way a failed PSU would.

   ```bash
   ./scripts/benchctl set node cn001 off
   ./scripts/benchctl set node cn001 on
   ```

## You should see

- `sinfo` shows `POWERED_DOWN`, then `POWERING_UP`, then the node idle again.
- The BMC reports `PowerState` `Off`, and the Redfish log shows `Reset ForceOff` then `Reset On` for the reboot.
- After `set node cn001 off`, Slurm marks the node DOWN after about 30 seconds.

## Next

- [Power and reboot](../guides/power-and-reboot.md): the states and the BMC wiring.
- [Break a node](03-break-a-node.md).
