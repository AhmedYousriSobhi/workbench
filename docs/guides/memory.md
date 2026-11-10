# How much memory the workbench uses

Measured on 2026-11-05 with `docker stats` on a live stack (issue 287). The plan (`benchctl plan`, `up`) uses these numbers: **expected use = measured + 15 percent, never above the cap**. Numbers live in `benchctl/memory.py`; the contract and the method are in the [memory contract](../architecture/04-memory-and-profiles.md).

## What you need free

| You start | Expected use | With the 1 GB app reserve | Free memory it needs (floor 1 GB) |
|---|---|---|---|
| `min` preset (4 nodes, mocks, scenarios) | 0.5 GB | 1.5 GB | 2.5 GB |
| `std` preset | 1.3 GB | 2.3 GB | 3.3 GB |
| 2 cpu + 2 gpu nodes, every add-on except the real Jira | 1.1 GB | 2.1 GB | 3.1 GB |
| the same plus the real Jira (`--with jira-real`) | 3.4 GB | 4.4 GB | 5.4 GB |

The app under test (your monitoring client) is reserved at **1 GB** until you measure it on your machine: the reserve is for the client alone, not part of any pod, and the plan prints it as `assumed, not measured`. The 1 GB default comes from one measured session (below); it was 4 GB, a guess, before. Measure it once, with the client running and in use:

```bash
./scripts/benchctl measure app --match NAME --duration 120 --save
```

`NAME` is any part of the client's process name or command line (`--pid N` also works; both repeat). It first prints which programs matched (`matched: electron x7 (460 MB), ...`) and warns when more than 4 different ones do, because a loose word such as `name` also matches unrelated programs and inflates the result; read that line before `--save`. It sums the PSS of every matching process, prints peak, typical use and a suggested reserve (peak + 10%, rounded up to 100 MB), and `--save` keeps it in `compose/generated/app_reserve.json` (gitignored). The plan then says `measured on DATE`. Delete the file to return to 1024. One measured session (a desktop client with one cluster, a terminal, the real Jira and every view opened during the `client-stress` scenario): 469 MB idle, 664 MB peak, so 800 MB; the default is 1024 (664 MB plus room for a second cluster or terminal), and a session with several clusters should be measured on its own. Precedence: `--app-mb`, then `WORKBENCH_APP_MB`, then the saved measurement, then 1024. If it already runs, its memory is already out of the free figure: add `--app-mb 0` to `plan`, `up`, `apply` or `start` (or set `WORKBENCH_APP_MB=0`).

## What each part really uses

Largest working set seen (MB), the cap the container may reach, and what the plan expects:

| Part | Measured | Cap | Expected |
|---|---|---|---|
| each compute node (slurmd) | 13 | 60 | 15 |
| each node's BMC address | 18 | 32 | 21 |
| bmc-farm / bcm-mock / jira-mock / login | 14 / 12 / 20 / 10 | 100 / 100 / 100 / 40 | 17 / 14 / 23 / 12 |
| slurmctld / slurmrestd / slurmdbd | 41 / 7 / 7 | 200 / 60 / 80 | 48 / 9 / 9 |
| MariaDB (accounting) | 204 | 250 | 235 |
| Teleport | 222 | 320 | 256 |
| Prometheus / Grafana / GPU exporter | 22 / 54 / 19 | 300 / 300 / 100 | 26 / 62 / 22 |
| **Jira (real), Data Center** | **2045** | **2176** | **2176** |
| Postgres for the real Jira | 130 | 256 | 150 |
| scenario engine, user-sim, ticket-bot | not measured (not built) | 60 / 60 / 40 | the cap |

The real Jira is more than 90 percent of what the whole stack uses. Its cap of 2176 MB (heap 768 MB, a trimmed JVM: Serial GC, C1-only compilation, 512 KB thread stacks) passed the setup and a ticket run twice with a peak of about 2040 MiB; without the JVM flags the smallest working cap was 2560 MB. A Java application keeps its heap, code cache, plugin system and thread stacks, so it cannot go much lower. All trials: [real Jira](jira-real.md#why-21-gb-measured-issues-287-and-289).

Switching Jira's plugins off was tried and does not save memory (the safe groups change nothing, the others stop Jira starting): [measured](jira-real.md#switching-plugins-off-does-not-help-measured-issue-289).

## Reading `benchctl plan`

```text
Memory, MB: expected use (measured + 15%, memory.py) and the cap the container may reach
                     expected      cap
    4 nodes                 60      240
  jira-real              2176     2176
  total                  3394     4876
  app under test         1024   (assumed, not measured)
  needs                  7490   (admitted against this)
```

- **expected** is what is forecast; **cap** is the limit the kernel enforces and the governor protects.
- **needs** is expected use plus the app's reserve. The verdict compares it with free memory and a 1 GB floor.
- A start that does not fit tells you what to drop, and says `--app-mb 0` when the app already runs.

## Why a start can still be stopped

The numbers above are sizes. A start can also be stopped by the governor on a short memory stall even with free memory left (a Java start briefly stalls). That is issue 286. Close the browser and IDE first, or leave the real Jira off and use the mock (20 MB).

## Docker's own memory (issue 456)

`docker stats` shows what is inside each container. Docker adds memory outside them: one `containerd-shim` process per container, plus `dockerd` and `containerd`. The shims grow with every node you add, so the plan counts them once measured:

```bash
./scripts/benchctl up                      # the stack must be running
./scripts/benchctl measure overhead --save # MB per container, MB fixed; kept in compose/generated (gitignored)
./scripts/benchctl plan                    # now has a "docker overhead" line
```

Until you run it the plan adds 0 and says "not measured" on that line; it never invents a figure. Root-owned shims hide their exact (proportional) size from a normal user, so the command uses their private memory (anonymous plus shared-memory pages) and says so; on the first live run that gave 4 MB per shim, the same as the exact figure from `sudo ./scripts/benchctl measure overhead` (do not `--save` with sudo). Plain VmRSS was tried first and gave 11 MB, because it counts the shared libraries in every shim. `benchctl measure run` records the same numbers and `measure report` prints them under the table.

Every part that is multiplied by the node count declares its cost per node in `PER_NODE_MB` (`benchctl/memory.py`): slurmd 60 MB, each node's BMC address 32 MB, and the optional per-node DCGM exporter 150 MB. `tests/test_overhead.py` generates two cluster sizes, finds what was added per node, and fails when a part is missing from the table or its cap or measurement is above the declared cost.

## Measuring again

1. Start the selection, then run `docker stats --no-stream` a few times, with load on if you want the loaded figure.
2. Put the largest value per component in `MEASURED_MB` and the date in `MEASURED_ON` (`benchctl/memory.py`).
3. Update the tables here and in the architecture doc. `tests/test_memory.py` fails when a component has no measurement or a measurement above its cap.
