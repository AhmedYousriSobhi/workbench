# Memory contract and profiles

[Architecture index](README.md) · [Project overview](../../README.md)

## 5. Memory contract (planning estimates; measure in Phase 0)

Host reserve (OS, desktop, IDE, browser): **about 3 GB**, never governed. Page cache and bursts need headroom, so target **at most about 9 GB** for the sim.

| Component | Est. RAM | Priority |
|---|---|---|
| governor + simctl + Web UI (< 50 MB) | 0.15 GB | P0 (protected) |
| Prometheus + Alertmanager + Grafana | 0.7 GB | P1 |
| slurmctld + slurmrestd | 0.2 GB | P1 |
| slurmdbd + MariaDB (small buffer pool) | 0.2 GB | P2 (profile-gated) |
| App under test | 1.0 GB (budget) | P1 |
| bmc-farm + bcm-mock + jira-mock + gpu-exporter-farm | 0.3 GB | P2 |
| k3d/kind (1 server + 1 agent) + KWOK | 1.2 GB | P3 |
| slurmd containers | ~60 MB each (16 nodes about 1 GB) | P4 |
| user-sim + ticket-bot + scenario-engine | 0.15 GB | P5 |
| Fidelity VM | 2.0 GB cap | P6 |
| Jira DC (jira-real only) | 3.0 GB+ | P6 |

**Profiles**

| Profile | What's on | Est. sim total |
|---|---|---|
| `min` | core, mocks, 4 slurmd, scenarios | ~1 GB (measured 0.17 GB loaded, rest is headroom for unbuilt mocks) |
| `std` | `min` + accounting, 16 slurmd, k3d + KWOK, load sims | ~6 GB |
| `full` | `std` + 32 slurmd, 200 KWOK nodes, fidelity VM | ~8.5 GB |
| `jira-real` | `std` minus k3d, plus Jira DC | ~7.5 GB |
| `mac-agent` | `min` + remote macOS agent (external) | ~1 GB local |

`simctl up --profile X` runs a **pre-flight**: `MemAvailable` minus the profile's declared budget must stay above the safety floor, otherwise it refuses and suggests a smaller profile.
