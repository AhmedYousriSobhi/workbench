# 22. A switch fails

**You will:** give Slurm a switch topology and fail one leaf switch. About 10 minutes.

## You need

- A cluster built with a `topology:` key in `sim.yaml` (uncomment the block; the default is off, with no extra memory).

## Steps

1. Uncomment the `topology:` block in `sim.yaml`: plugin `tree`, 1 spine, 2 leaves per spine.

2. Rebuild.

   ```bash
   ./scripts/benchctl up
   ```

3. Read the topology Slurm uses.

   ```bash
   docker exec workbench-slurmctld-1 scontrol show topology
   ```

4. Fail leaf switch `leaf1`.

   ```bash
   ./scripts/benchctl scenario run switch-leaf-down
   ```

5. See the drained nodes and the reason.

   ```bash
   ./scripts/benchctl get nodes
   ```

## You should see

- Exactly the nodes under `leaf1` drain, with the reason `switch leaf1 down`.
- At 90 seconds the switch is back, and only those nodes resume.

## Next

- [Topology](../guides/topology.md): the `block` plugin, and how nodes spread over leaves.
