# 22. A switch fails

**You will:** give Slurm a switch topology and fail one leaf switch. About 10 minutes.

## You need

- A cluster built with a `topology:` key in `sim.yaml` (uncomment the block; the default is off, with no extra memory).

## Steps

1. Uncomment the `topology:` block in `sim.yaml` (lines `topology:`, `plugin: tree`, `spines: 1`, `leaves_per_spine: 2`: remove the leading `# `). Undo it afterwards with `git checkout sim.yaml`; do not commit it.

2. Rebuild. A few seconds on a running cluster; it restarts `slurmctld` by itself because `topology.conf` changed.

   ```bash
   ./scripts/benchctl up
   ```

3. Read the topology Slurm uses. With 16 CPU and 2 GPU nodes you get `leaf1` (`cn001,003,...,015` and `gpu001`), `leaf2` (the even ones and `gpu002`) and `spine1` over both: nodes are spread round-robin.

   ```bash
   docker exec workbench-slurmctld-1 scontrol show topology
   ```

4. Fail leaf switch `leaf1`. It takes 90 s and ends with `scenario passed`; run step 5 in a second terminal about 15 s in.

   ```bash
   ./scripts/benchctl scenario run switch-leaf-down
   ```

5. See the drained nodes and the reason. `get nodes` only shows containers (`running`), so ask Slurm.

   ```bash
   docker exec workbench-slurmctld-1 sinfo -R
   ```

## You should see

- Exactly the nodes under `leaf1` drain: `sinfo -R` lists `switch leaf1 down` with their names.
- At 90 seconds the switch is back, and only those nodes resume: `sinfo -R` prints only its header.

## Next

- [Topology](../guides/topology.md): the `block` plugin, and how nodes spread over leaves.
