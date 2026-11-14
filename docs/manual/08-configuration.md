<p align="center"><img src="../assets/art/08-configuration-hero.svg" alt="One file, sim.yaml, with arrows to the generated compose file, slurm.conf and targets" width="720"></p>

# 8. Configuration

**In 30 seconds:** you edit one file, `sim.yaml`, and everything else (compose, Slurm config, Prometheus targets, BMC inventories) is generated from it. Never edit the generated files by hand.

## Do it

```bash
./scripts/benchctl compose --profile min      # regenerate and look at the result
```

Then bring the stack down and up again so every container sees the same values.

[sim.yaml](../../sim.yaml) is the single source of truth. Compose files, `slurm.conf`, Prometheus targets and the BMC and BCM
inventories are generated from it, so node names agree everywhere. **Never hand-edit generated files**
(`compose/generated/`, `slurm.conf`); change `sim.yaml` or a profile and regenerate.

## sim.yaml

| Key | Meaning |
|---|---|
| `time_dilation` | above 1 compresses job durations and arrival intervals |
| `jira.mode` | `mock` (default), `cloud`, `dc` |
| `compute` | `count`, `prefix` (names `cn001`...), `memory_mb` and `cpus` (the fake size Slurm advertises) |
| `gpu` | `nodes`, `prefix` (`gpu001`...), `gpus_per_node`, `type`; optional `mig: {nodes: [gpu002], profile: 1g, slices: 7}` splits every GPU of the listed nodes into slices (Slurm then advertises `gpu:h200_1g:56` there; request it with `--gres=gpu:h200_1g:N`; off by default) |
| `partitions` | name and which nodes (`cpu`, `gpu`) |
| `users` | name, persona, QOS; UIDs are assigned in order from 1001 |
| `kubernetes` | `version` (Kubernetes release of the control plane), `nodes`: groups of KWOK nodes with `prefix`, `count`, `cpus`, `memory_gb`, `pool` and, for GPU nodes, `gpus` and `gpu_type` (the optional Kubernetes part, [chapter 4](04-nodes-and-shells.md)) |
| `bmc.per_node` | one Redfish endpoint per node |
| `host` | `reserve_gb` and `safety_floor_gb`: memory kept for the host |

## Profiles

`profiles/NAME.yaml` has a `budget_gb`, an `extends` list, the component `groups` and `compute_nodes`. Groups are the union
across `extends`; other keys come from the most specific profile. The groups are `core`, `accounting`, `mocks`,
`scenarios` and `load`; the list lives in `CATALOG` in `benchctl/compose.py`.

## Common changes

```bash
./scripts/benchctl compose --profile min       # regenerate compose/generated/docker-compose.yml and look at it
./scripts/benchctl scale 8                      # change the compute node count on a running stack
./scripts/benchctl down && ./scripts/benchctl up --profile std      # change profile
```

After editing `sim.yaml` (users, GPUs per node, names), bring the stack down and up again so every container sees the
same values. Names that do not exist are rejected by `scenario run --dry-run`.

## Memory budgets

Budgets for `std` and `full` are estimates until measured. To measure: `benchctl measure run idle`, start load,
`benchctl measure run loaded --duration 120`, then `benchctl measure report idle loaded`. Results go to `docs/measurements/`
(not committed) with suggested budgets (peak times 1.2). See [the memory contract](../architecture/04-memory-and-profiles.md).

## Secrets

Generated secrets (database password, the governor's Grafana token, the web page token) live only in
`compose/generated/secrets/` (not committed). Never copy them into files, messages or reports.

## Under the hood

> [!HOOD]
> `benchctl compose` reads `sim.yaml` plus `profiles/NAME.yaml`, builds the compose model from a component catalogue (`CATALOG`) and writes `compose/generated/docker-compose.yml` with a "GENERATED, do not edit" header. A copy of the `sim.yaml` it used is written beside it, because `scale` and a saved workbench selection change the counts, so the repository file is the default and not the running truth.
> Every component gets a memory limit and a priority class label (`workbench.class: P0` to `P6`) from that catalogue, which is how the governor knows what to pause first.
> `scale` changes only the node count of a running stack; for any other `sim.yaml` change, bring the stack down and up again so every container sees the same values.

Source: `benchctl/compose.py` (`CATALOG`, `write`), `benchctl/stack.py` (`scale`), SPEC section 3

## Tips

> [!WARNING]
> Files under `compose/generated/` are overwritten on every run. A hand edit there is lost.

## If it goes wrong

| Symptom | Try |
|---|---|
| A change did not show up | `benchctl down`, then `benchctl up` |
| `scenario run` rejects a name | the node or user is not in `sim.yaml`; use `--dry-run` to see which |

## Learn more

- [sim.yaml](../../sim.yaml)
- [Choose your workbench](../guides/choose-workbench.md)
- [The memory contract](../architecture/04-memory-and-profiles.md)
