# How much memory the workbench uses

Measured on 2026-11-05 with `docker stats` on a live stack (issue 287). The plan (`benchctl plan`, `up`) uses these numbers: **expected use = measured + 15 percent, never above the cap**. Numbers live in `benchctl/memory.py`; the contract and the method are in the [memory contract](../architecture/04-memory-and-profiles.md).

## What you need free

| You start | Expected use | With the 4 GB app reserve | Free memory it needs (floor 1 GB) |
|---|---|---|---|
| `min` preset (4 nodes, mocks, scenarios) | 0.5 GB | 4.5 GB | 5.5 GB |
| `std` preset | 1.3 GB | 5.3 GB | 6.3 GB |
| 2 cpu + 2 gpu nodes, every add-on except the real Jira | 1.1 GB | 5.1 GB | 6.1 GB |
| the same plus the real Jira (`--with jira-real`) | 3.8 GB | 7.9 GB | 8.9 GB |

The app under test (your monitoring client) is reserved at **4 GB**. If it already runs, its memory is already out of the free figure: add `--app-mb 0` to `plan`, `up`, `apply` or `start` (or set `WORKBENCH_APP_MB=0`).

## What each part really uses

Largest working set seen (MB), the cap the container may reach, and what the plan expects:

| Part | Measured | Cap | Expected |
|---|---|---|---|
| each compute node (slurmd) | 13 | 60 | 15 |
| each node's BMC address | 18 | 32 | 21 |
| bmc-farm / bcm-mock / jira-mock / login | 14 / 12 / 20 / 10 | 100 / 100 / 100 / 40 | 17 / 14 / 23 / 12 |
| slurmctld / slurmrestd / slurmdbd | 41 / 7 / 7 | 200 / 60 / 80 | 48 / 9 / 9 |
| MariaDB (accounting) | 204 | 250 | 235 |
| Teleport | 222 | 256 | 256 (at its cap) |
| Prometheus / Grafana / GPU exporter | 22 / 54 / 19 | 300 / 300 / 100 | 26 / 62 / 22 |
| **Jira (real), Data Center** | **2363** | **2560** | **2560** |
| Postgres for the real Jira | 130 | 256 | 150 |
| scenario engine, user-sim, ticket-bot | not measured (not built) | 60 / 60 / 40 | the cap |

The real Jira is more than 90 percent of what the whole stack uses. Its cap of 2560 MB is the smallest that finished the setup and a ticket run: at 2304 MB it was killed once, at 2048 and 1536 MB setup failed. A Java application keeps its heap, its code cache, its plugin system and thread stacks, so the cap cannot go much below 2.5 GB. See [real Jira](jira-real.md#why-25-gb-measured-issue-287).

## Reading `benchctl plan`

```text
Memory, MB: expected use (measured + 15%, memory.py) and the cap the container may reach
                     expected      cap
    4 nodes                 60      240
  jira-real              2560     2560
  total                  3778     5260
  app under test         4096
  needs                  7874   (admitted against this)
```

- **expected** is what is forecast; **cap** is the limit the kernel enforces and the governor protects.
- **needs** is expected use plus the app's reserve. The verdict compares it with free memory and a 1 GB floor.
- A start that does not fit tells you what to drop, and says `--app-mb 0` when the app already runs.

## Why a start can still be stopped

The numbers above are sizes. A start can also be stopped by the governor on a short memory stall even with free memory left (a Java start briefly stalls). That is issue 286. Close the browser and IDE first, or leave the real Jira off and use the mock (20 MB).

## Measuring again

1. Start the selection, then run `docker stats --no-stream` a few times, with load on if you want the loaded figure.
2. Put the largest value per component in `MEASURED_MB` and the date in `MEASURED_ON` (`benchctl/memory.py`).
3. Update the tables here and in the architecture doc. `tests/test_memory.py` fails when a component has no measurement or a measurement above its cap.
