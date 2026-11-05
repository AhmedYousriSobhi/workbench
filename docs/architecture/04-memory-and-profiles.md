# Memory contract and profiles

[Architecture index](README.md) · [Project overview](../../README.md)

## 5. Memory contract (measured, issue 287)

Host reserve (OS, desktop, IDE, browser): **about 3 GB**, never governed. Page cache and bursts need headroom, so target **at most about 9 GB** for the sim.

| Component | Est. RAM | Priority |
|---|---|---|
| governor + benchctl + Web UI (< 50 MB) | 0.15 GB | P0 (protected) |
| Prometheus + Alertmanager + Grafana | 0.7 GB | P1 |
| slurmctld + slurmrestd | 0.2 GB | P1 |
| slurmdbd + MariaDB (small buffer pool) | 0.2 GB | P2 (profile-gated) |
| App under test | 1.0 GB assumed until measured on your machine (664 MB peak in one session) (`benchctl measure app --save`, `--app-mb`) | P1 |
| bmc-farm + bcm-mock + jira-mock + gpu-exporter-farm | 0.3 GB | P2 |
| k3d/kind (1 server + 1 agent) + KWOK | 1.2 GB | P3 |
| slurmd containers | ~60 MB each (16 nodes about 1 GB) | P4 |
| user-sim + ticket-bot + scenario-engine | 0.15 GB | P5 |
| Fidelity VM | 2.0 GB cap | P6 |
| Jira DC (jira-real only) | 2.1 GB cap + Postgres 0.25 GB (measured, trimmed JVM) | P6 |

**Profiles**

| Profile | What's on | Est. sim total |
|---|---|---|
| `min` | core, mocks, 4 slurmd, scenarios | 0.5 GB expected (measured use plus 15 percent) |
| `std` | `min` + accounting, 16 slurmd, k3d + KWOK, load sims | 1.3 GB expected today (k3d and KWOK are not built; they will add their measured size) |
| `full` | `std` + 32 slurmd, 200 KWOK nodes, fidelity VM | 1.9 GB expected today (32 slurmd; KWOK and the fidelity VM not built) |
| `jira-real` | `std` minus k3d, plus Jira DC | 3.6 GB expected |
| `mac-agent` | `min` + remote macOS agent (external) | 0.5 GB local |

`benchctl up --profile X` runs a **pre-flight**: `MemAvailable` minus the profile's budget and the app under test's reserve must stay above the safety floor, otherwise it refuses and suggests a smaller profile.

### How the plan estimates (issue 287)

`benchctl plan` and `up` used to admit against the container **caps**: 5.1 to 5.8 GB for a selection that used 3.0 GiB. A cap is
a limit, not a forecast. Now every component has the **largest working set seen in a live run** (`benchctl/memory.py`,
`MEASURED_MB`; docker stats, 2026-11-05: 2 cpu + 2 gpu nodes with accounting, bmc, bcm, jira, teleport, scenarios and load on,
the real Jira on its own), the plan **expects measured + 15 percent, never above the cap**, and a component nobody has measured
is expected at its cap. `plan` prints both columns, the governor still enforces the caps, and a test fails when a component is
added without a measurement.

| Component | Measured MB | Cap MB | Expected MB |
|---|---|---|---|
| slurmd (each compute node) | 13 | 60 | 15 |
| BMC forwarder (each) | 18 | 32 | 21 |
| bmc-farm / bcm-mock / jira-mock / login | 14 / 12 / 20 / 10 | 100 / 100 / 100 / 40 | 17 / 14 / 23 / 12 |
| slurmctld / slurmrestd / slurmdbd | 41 / 7 / 7 | 200 / 60 / 80 | 48 / 9 / 9 |
| mariadb (includes its 100 MB tmpfs) | 204 | 250 | 235 |
| teleport | 222 | 256 | 256 (at the cap: raise it with the next measurement) |
| prometheus / grafana / gpu-exporter-farm | 22 / 54 / 19 | 300 / 300 / 100 | 26 / 62 / 22 |
| jira-db (real Jira's Postgres) | 130 | 256 | 150 |
| jira-real | 2045 (peak, setup plus ticket run, trimmed JVM) | 2176 | 2176 (at the cap by nature: a JVM keeps what it grew) |
| scenario-engine, user-sim, ticket-bot, accounting-init | not measured (not built or one-shot) | 60 / 60 / 40 / 30 | the cap |
| **app under test** | | | **1024 (reserve)** |

The 15 percent tolerance is `memory.TOLERANCE`. Live check on the same selection (plan expected 1068 MB; 190 MB of that is
components that are not built; 728 MiB in use): the plan sits about 17 percent above use. The app's reserve is part of
`need`, the number admission compares with free memory; leave it out with `--app-mb 0` (or `WORKBENCH_APP_MB=0`) when the
app is already running, because its memory is then already out of `MemAvailable`. To re-measure: start the selection, run
`docker stats --no-stream`, edit `MEASURED_MB` and `MEASURED_ON`, and update the table above.
