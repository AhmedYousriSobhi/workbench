# ADR 0053: Slurm topology from sim.yaml and the switch scenario step

Status: accepted. Issue 543; epic 557 (config lab A-5). Builds on ADR 0051 (one node inventory).

## Decision

- Optional `topology:` in `sim.yaml` (`plugin: tree|block`, `spines`, `leaves_per_spine`, `block_size` for block). No key, no change: Slurm stays flat and no file is written (an old `topology.conf` is deleted).
- `benchctl/topology.py` is the one place that decides which node sits under which leaf: the Slurm nodes of the inventory (compute and GPU, not the real-GPU card) in order, node i under leaf `i mod leaves`, so each leaf holds a mix and a switch failure is a rack failure, not a partition. It writes `topology.conf` beside `slurm.conf`; `slurmconf.py` adds `TopologyPlugin=topology/<plugin>`. The profile's node count counts (the generated `sim.yaml` copy carries it), and `compose.generate` refuses a topology with fewer nodes than leaves before anything is written.
- Slurm 26.05 still reads `topology.conf` (the file is ignored only when a `topology.yaml` exists); we use it because it is what a production site exports and what the config lab (C-24) imports.
- A changed `topology.conf` restarts `slurmctld` like a changed `slurm.conf` (it is only read at start).
- Scenario target `switch`, `{leaf, state: down|up}`, is a Slurm step: down drains the leaf's nodes with reason `switch LEAF down`, up resumes exactly those. It reuses the Slurm step, so the page plays it through the governor like any drain.

## Consequences

Placement with `--switches` is the real plugin's. Leaf names are fixed (`leaf1..`), spines `spine1..`, `root` over several spines. Not covered: dragonfly, torus and ring plugins; per-link speeds; a leaf failing without its nodes noticing (Slurm has no switch state, so a switch outage is modelled as the drain an operator would do).
