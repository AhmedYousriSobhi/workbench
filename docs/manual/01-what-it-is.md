<p align="center"><img src="../assets/art/01-what-it-is-hero.svg" alt="A cloud of stand-in services and a rack of nodes with the governor badge beside them" width="720"></p>

# 1. What workbench is

**In 30 seconds:** workbench is a pretend HPC cluster for one 16 GB laptop. Most of what a monitoring app talks to is an API, so workbench fakes the APIs, runs real Slurm in small containers, and keeps a governor outside everything that stops the least important parts before memory runs out.

## Do it

Read this chapter once, then start the cluster with `./scripts/benchctl start` ([chapter 2](02-install-and-start.md)). Everything below explains what you will be looking at.

workbench simulates an HPC/AI cluster on one **16 GB Ubuntu workstation** so the application under test has a
realistic cluster to talk to: Slurm, BMCs, BCM, Jira, Grafana, with users, jobs, faults and tickets. A **governor**
runs outside everything and stops low-priority parts before memory runs out.

## Why it is cheap

Most of what the app talks to is an API, not an operating system. So:

- One process (`bmc-farm`) answers for every node's Redfish BMC, and one (`gpu-exporter-farm`) for every GPU's metrics.
- Slurm is real (built from source) but every `slurmd` is a small container from one shared image, and jobs are synthetic.
- A real VM (the fidelity VM) is deferred: no client endpoint needs it (ADR 0033).
- Everything is generated from [sim.yaml](../../sim.yaml), so node names agree everywhere.

## The parts

| Part | What it is | Where you reach it |
|---|---|---|
| `slurmctld`, `slurmrestd` | the Slurm controller and its REST API | `docker exec workbench-slurmctld-1 ...` |
| `cn001`... `gpu001`... | simulated compute and GPU nodes, each a `slurmd` container | `docker exec -it workbench-cn001-1 bash` ([chapter 4](04-nodes-and-shells.md)) |
| `slurmdbd`, `mariadb` | Slurm accounting (the `accounting` part: on in a default workbench, off in the `min` profile) | inside the stack |
| `bmc-farm` | fake Redfish BMCs, one endpoint per node | http://127.0.0.1:8000 |
| `bcm-mock` | fake BCM (CMDaemon JSON); the login node also answers `cmsh -c "device; list"` from its state (ADR 0013) | http://127.0.0.1:8081 |
| `jira-mock` | fake Jira REST with failure modes | http://127.0.0.1:8080 |
| `gpu-exporter-farm` | fake DCGM GPU metrics | http://127.0.0.1:9400 |
| Prometheus, Grafana | metrics and dashboards | :9090, :3000 |
| Web page | one dashboard over everything | http://127.0.0.1:8088 |
| Governor | memory protection on the host | `benchctl status` |
| `benchctl scenario`, `benchctl load` | timed faults and simulated users (host CLI and login node, no container) | see [chapter 6](06-scenarios.md) |

Everything listens on `127.0.0.1` only, except the governor's metrics, which bind the Docker bridge address. **Not built yet:** the Profile and Budget manager
page, the user simulator and ticket bot. Teleport access is an optional part ([chapter 4](04-nodes-and-shells.md)). See [PROGRESS.md](../PROGRESS.md).

## Profiles

A profile chooses which parts run and carries a memory budget that `benchctl up` enforces.

| Profile | What is on | Budget |
|---|---|---|
| `min` | core, mocks, 4 compute nodes, scenarios | 0.5 GB expected |
| `std` | `min` plus accounting, 16 compute nodes, load simulators | 1.2 GB expected |
| `full` | `std` plus 32 compute nodes, more fake nodes | 1.8 GB expected |
| `jira-real` | `std` with a real Jira Data Center instead of the mock | 3.5 GB expected |
| `mac-agent` | `min` plus an external macOS agent | 0.5 GB local |

The quick start uses `min`. `up` refuses a profile that does not fit in the memory free right now and names one that does.

## Priority classes

Every container declares a memory limit and a class, **P0 to P6**. When memory is tight the governor acts on the highest
number first: P6 (VM, Jira DC), P5 (load), P4 (compute nodes), P3 (Kubernetes), P2 (mocks), P1 (Slurm core, app). P0 is
the governor and the web page, and is never touched. In the stack today: slurmctld and slurmrestd P1, mocks and
accounting P2, compute nodes P4, load simulators P5.

## The one rule about privilege

Containers are unprivileged (no added capabilities, no Docker socket) and memory-limited. The web page has no Docker
access; it only talks to the governor's socket. Nothing here needs `sudo`; the optional root governor service is the one
exception you may choose ([chapter 7](07-governor.md)).

## Under the hood

One process plays all the BMCs and one plays all the GPU exporters, so a node costs a few megabytes, not a machine. The Slurm part is the real thing, which is why `sinfo`, node states and `slurmrestd` authentication behave as on a real cluster.

> [!HOOD]
> The BMC farm is one process that picks the node from the request's `Host` header (`cn001` or `bmc-cn001`), and the profile budgets are measured use plus 15 percent, never above a container's cap. Slurm is built from source in the node base image; GPUs, cgroups and jobs are simulated.

Source: `components/bmc-farm/bmc.py`, `benchctl/memory.py`, `profiles/*.yaml`, ADR 0002, ADR 0018, ADR 0025.

## Tips and gotchas

> [!TIP]
> Not sure a profile fits? `./scripts/benchctl preflight --profile std` says so before anything starts.

> [!WARNING]
> The numbers in the profile table are what `benchctl up` enforces. Do not start a profile on a laptop that is already busy; `up` will refuse and name one that fits.

## If it goes wrong

| You see | Why | Do this |
|---|---|---|
| `up` refuses a profile | it does not fit in the memory free right now | close a few programs, or use the smaller profile it names |

## Learn more

[Install and start](02-install-and-start.md) · [The governor](07-governor.md) · [Memory guide](../guides/memory.md) · [Architecture overview](../architecture/02-architecture.md)
