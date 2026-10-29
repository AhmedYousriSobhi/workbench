# opti-sim user manual

The complete guide to using opti-sim. Read it front to back once, then use it as a reference.
[SPEC.md](../../SPEC.md) says what the tool must do, [the architecture](../architecture/README.md) says why, and
[what works today](../PROGRESS.md) says what is built. If this manual and SPEC.md disagree, SPEC.md wins.

| # | Chapter | You will learn |
|---|---|---|
| 1 | [What opti-sim is](01-what-it-is.md) | the idea, the parts, the profiles, the ports |
| 2 | [Install and start](02-install-and-start.md) | prerequisites, first start, stop, status |
| 3 | [Using the cluster](03-using-the-cluster.md) | up, scale, down, Slurm jobs, the mock services, Grafana |
| 4 | [Nodes and shells](04-nodes-and-shells.md) | **how to open a shell on a node** (there is no `ssh`), users, the fidelity VM |
| 5 | [The web page](05-web-page.md) | login, panels, fault actions, scenarios |
| 6 | [Scenarios and faults](06-scenarios.md) | play, fast-forward, inject by hand, write your own |
| 7 | [The governor](07-governor.md) | memory protection, levels, what gets paused or stopped |
| 8 | [Configuration](08-configuration.md) | `sim.yaml`, profiles, changing the topology |
| 9 | [Troubleshooting](09-troubleshooting.md) | symptoms, causes, fixes, the diagnostics bundle |
| 10 | [Reference](10-reference.md) | every command, script, port, file and environment variable |

Short on time? [Getting started](../guides/getting-started.md) is the 10-minute version.

## Conventions

- Commands run from the repository root. `./scripts/simctl <cmd>` is `python -m simctl.cli <cmd>` without
  activating the virtualenv.
- Nothing in the normal start, use and stop path needs `sudo` (ADR 0010).
- "Node" means a simulated node: a container (a compute or GPU node running `slurmd`), not a machine.
- Parts that are not built yet are marked **not built**; [PROGRESS.md](../PROGRESS.md) is the live list.
