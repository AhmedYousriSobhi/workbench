# 8. Configuration

[sim.yaml](../../sim.yaml) is the single source of truth. Compose files, `slurm.conf`, Prometheus targets and the BMC and BCM
inventories are generated from it, so node names agree everywhere. **Never hand-edit generated files**
(`compose/generated/`, `slurm.conf`); change `sim.yaml` or a profile and regenerate.

## sim.yaml

| Key | Meaning |
|---|---|
| `time_dilation` | above 1 compresses job durations and arrival intervals |
| `jira.mode` | `mock` (default), `cloud`, `dc` |
| `compute` | `count`, `prefix` (names `cn001`...), `memory_mb` and `cpus` (the fake size Slurm advertises) |
| `gpu` | `nodes`, `prefix` (`gpu001`...), `gpus_per_node`, `type` |
| `partitions` | name and which nodes (`cpu`, `gpu`) |
| `users` | name, persona, QOS; UIDs are assigned in order from 1001 |
| `kubernetes` | `real_agents`, `kwok_nodes` (not built yet) |
| `bmc.per_node` | one Redfish endpoint per node |
| `host` | `reserve_gb` and `safety_floor_gb`: memory kept for the host |

## Profiles

`profiles/NAME.yaml` has a `budget_gb`, an `extends` list, the component `groups` and `compute_nodes`. Groups are the union
across `extends`; other keys come from the most specific profile. The groups are `core`, `accounting`, `mocks`,
`scenarios` and `load`; the list lives in `CATALOG` in `simctl/compose.py`.

## Common changes

```bash
./scripts/simctl compose --profile min       # regenerate compose/generated/docker-compose.yml and look at it
./scripts/simctl scale 8                      # change the compute node count on a running stack
./scripts/simctl down && ./scripts/simctl up --profile std      # change profile
```

After editing `sim.yaml` (users, GPUs per node, names), bring the stack down and up again so every container sees the
same values. Names that do not exist are rejected by `scenario run --dry-run`.

## Memory budgets

Budgets for `std` and `full` are estimates until measured. To measure: `simctl measure run idle`, start load,
`simctl measure run loaded --duration 120`, then `simctl measure report idle loaded`. Results go to `docs/measurements/`
(not committed) with suggested budgets (peak times 1.2). See [the memory contract](../architecture/04-memory-and-profiles.md).

## Secrets

Generated secrets (database password, the governor's Grafana token, the web page token) live only in
`compose/generated/secrets/` (not committed). Never copy them into files, messages or reports.
