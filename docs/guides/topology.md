<p align="center"><img src="../assets/art/guide-topology-hero.svg" alt="A spine switch over two leaf switches, each holding a small rack of nodes" width="720"></p>

# Slurm topology and a switch going down

**In 30 seconds:** give Slurm a real switch tree from `sim.yaml`, then take a leaf switch down to see its nodes drain with the reason `switch leaf1 down`. It is off until you add a `topology:` key.

## Do it

```bash
./scripts/benchctl up      # after adding topology: to sim.yaml
docker exec workbench-slurmctld-1 scontrol show topology
./scripts/benchctl scenario run switch-leaf-down
```

The real Slurm topology plugin, with switches generated from `sim.yaml` (issue 543, ADR 0053). Off by default: with no `topology:` key Slurm stays flat and nothing below changes. No extra process and no extra memory: it is one more generated file read by `slurmctld`.

## Turn it on

Edit [sim.yaml](../../sim.yaml) (the commented `topology:` block is there to copy):

```yaml
topology:
  plugin: tree          # tree (default) | block
  spines: 2             # 1 to 8
  leaves_per_spine: 2   # 1 to 16
```

Then:

```bash
./scripts/benchctl up      # regenerates compose/generated/slurm/topology.conf; a changed file restarts slurmctld by itself
docker exec workbench-slurmctld-1 scontrol show topology
```

Compute and GPU nodes are spread round-robin over the leaves (`leaf1`, `leaf2`, ...), so every leaf holds a mix of nodes. With the default six nodes and `spines: 1, leaves_per_spine: 2`, `leaf1` holds `cn001, cn003, gpu001` and `leaf2` holds `cn002, cn004, gpu002`. `scontrol show topology` prints exactly these.

- `tree`: one switch per leaf, one switch per spine over its leaves, and a `root` over the spines when there is more than one. Jobs that ask for `--switches=1` stay under one leaf.
- `block`: one block per leaf (`BlockName=leafN`); the spines setting only sets how many blocks there are. `block_size` is the planning base size (default: the largest power of two every block can fill; at most the size of the smallest block).

One message per problem: `benchctl up` refuses with `FAILED topology: ...` when the plugin is unknown, a number is out of range or there are fewer nodes than leaves (the profile's node count counts, for example 16 compute nodes in `std`). Remove the key (or comment it out) and `up` again to go back to flat; the old file is deleted.

## A leaf switch goes down

```bash
./scripts/benchctl scenario run switch-leaf-down        # SC-35: leaf1 down for 90 s, then up
```

A step is `{target: switch, do: {leaf: leaf1, state: down}}` (or `up`):

- **down** drains exactly the nodes under that leaf, with reason `switch leaf1 down`; jobs already running on them finish, nothing new starts there.
- **up** resumes exactly the nodes of that leaf, including one you drained by hand under the same leaf. Nodes of other leaves are never touched, so a node drained elsewhere stays drained.

The step also works from the page's Test run (it goes through the governor like the other Slurm steps).

## What a client sees

`sinfo` shows the leaf's nodes `drain`/`drng` with the reason; `scontrol show topology` and `sinfo -N -l` stay the same. A client that reads topology should show the switch tree from `sim.yaml`; one that reads reasons should show `switch leafN down`. The Slurm job placement difference is real: submit `sbatch -N2 --switches=1` while a leaf is drained and it must fit under the other leaf.

## Checked

`tests/test_topology.py` (file text, node agreement with the generated inventory, the step's exact drain and resume commands) and `tests/real/test_topology_real.py` (`./scripts/verify.sh --unit topology`: `scontrol show topology` equals the plan; a leaf down drains exactly its nodes; up resumes only those). Not exercised: the `block` plugin on a live controller, `--switches` placement, and the monitoring client (see [client-testing.md](client-testing.md)).

## Under the hood

<p align="center"><img src="../assets/art/guide-topology-diagram.svg" alt="A spine over two leaves; one leaf is down and its nodes are drained with a reason" width="720"></p>

> [!HOOD]
> `benchctl/topology.py` decides which node sits under which leaf: the Slurm nodes of the inventory in order, node number i under leaf `i mod leaves`, so each leaf holds a mix and a switch failure is a rack failure, not a whole partition. It writes `compose/generated/slurm/topology.conf`.
> A changed file restarts `slurmctld` like a changed `slurm.conf`, because it is read only at start. Slurm has no switch state, so a switch outage is modelled as the drain of that leaf's nodes (scenario target `switch`); up resumes exactly those.

Source: `benchctl/topology.py` (`leaves`, `nodes_under`, `conf`), [ADR 0053](../adr/0053-slurm-topology-from-sim-yaml.md)

## Tips

> [!TIP]
> Try `sbatch -N2 --switches=1` while a leaf is drained: it has to fit under the other leaf.

## If it goes wrong

| Symptom | Try |
|---|---|
| `up` says `FAILED topology: ...` | the plugin is unknown, a number is out of range or there are fewer nodes than leaves; fix `sim.yaml` |
| Back to flat Slurm | remove the `topology:` key and run `up` again |

## Learn more

- [Scenarios](../manual/06-scenarios.md)
- [Client testing](client-testing.md)
