# 1. What opti-sim is

opti-sim simulates an HPC/AI cluster on one **16 GB Ubuntu workstation** so the application under test has a
realistic cluster to talk to: Slurm, BMCs, BCM, Jira, Grafana, with users, jobs, faults and tickets. A **governor**
runs outside everything and stops low-priority parts before memory runs out.

## Why it is cheap

Most of what the app talks to is an API, not an operating system. So:

- One process (`bmc-farm`) answers for every node's Redfish BMC, and one (`gpu-exporter-farm`) for every GPU's metrics.
- Slurm is real (built from source) but every `slurmd` is a small container from one shared image, and jobs are synthetic.
- One real VM (the fidelity VM) exists for what containers cannot prove; it is on demand only.
- Everything is generated from [sim.yaml](../../sim.yaml), so node names agree everywhere.

## The parts

| Part | What it is | Where you reach it |
|---|---|---|
| `slurmctld`, `slurmrestd` | the Slurm controller and its REST API | `docker exec hpcsim-slurmctld-1 ...` |
| `cn001`... `gpu001`... | simulated compute and GPU nodes, each a `slurmd` container | `docker exec -it hpcsim-cn001-1 bash` ([chapter 4](04-nodes-and-shells.md)) |
| `slurmdbd`, `mariadb` | Slurm accounting (profiles with accounting only) | inside the stack |
| `bmc-farm` | fake Redfish BMCs, one endpoint per node | http://127.0.0.1:8000 |
| `bcm-mock` | fake BCM (CMDaemon JSON); the login node also answers `cmsh -c "device; list"` from its state (ADR 0013) | http://127.0.0.1:8081 |
| `jira-mock` | fake Jira REST with failure modes | http://127.0.0.1:8080 |
| `gpu-exporter-farm` | fake DCGM GPU metrics | http://127.0.0.1:9400 |
| Prometheus, Grafana | metrics and dashboards | :9090, :3000 |
| Web page | one dashboard over everything | http://127.0.0.1:8088 |
| Governor | memory protection on the host | `benchctl status` |
| `scenario-engine`, `user-sim`, `ticket-bot` | timed faults, simulated users and engineers | see [chapter 6](06-scenarios.md) |

Everything listens on `127.0.0.1` only, except the governor's metrics, which bind the Docker bridge address. **Not built yet:** Kubernetes (k3d and KWOK), the Profile and Budget manager
page, the user simulator and ticket bot, Teleport/SSH access. See [PROGRESS.md](../PROGRESS.md).

## Profiles

A profile chooses which parts run and carries a memory budget that `benchctl up` enforces.

| Profile | What is on | Budget |
|---|---|---|
| `min` | core, mocks, 4 compute nodes, scenarios | 1 GB (measured 0.17 GB loaded) |
| `std` | `min` plus accounting, 16 compute nodes, load simulators | about 6 GB (estimate) |
| `full` | `std` plus 32 compute nodes, more fake nodes, the fidelity VM | about 8.5 GB (estimate) |
| `jira-real` | `std` with a real Jira Data Center instead of the mock | about 7.5 GB (estimate) |
| `mac-agent` | `min` plus an external macOS agent | about 1 GB local |

The quick start uses `min`. `up` refuses a profile that does not fit in the memory free right now and names one that does.

## Priority classes

Every container declares a memory limit and a class, **P0 to P6**. When memory is tight the governor acts on the highest
number first: P6 (VM, Jira DC), P5 (load), P4 (compute nodes), P3 (Kubernetes), P2 (mocks), P1 (Slurm core, app). P0 is
the governor and the web page, and is never touched. In the stack today: slurmctld and slurmrestd P1, mocks and
accounting P2, compute nodes P4, scenario-engine and load simulators P5.

## The one rule about privilege

Containers are unprivileged (no added capabilities, no Docker socket) and memory-limited. The web page has no Docker
access; it only talks to the governor's socket. Nothing here needs `sudo`; the optional root governor service is the one
exception you may choose ([chapter 7](07-governor.md)).
