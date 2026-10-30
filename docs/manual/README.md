# workbench user manual

The complete guide to using workbench. Read it front to back once, then use it as a reference.
[SPEC.md](../../SPEC.md) says what the tool must do, [the architecture](../architecture/README.md) says why, and
[what works today](../PROGRESS.md) says what is built. If this manual and SPEC.md disagree, SPEC.md wins.

| # | Chapter | You will learn |
|---|---|---|
| 1 | [What workbench is](01-what-it-is.md) | the idea, the parts, the profiles, the ports |
| 2 | [Install and start](02-install-and-start.md) | prerequisites, first start, stop, status |
| 3 | [Using the cluster](03-using-the-cluster.md) | up, scale, down, Slurm jobs, the mock services, Grafana |
| 4 | [Nodes and shells](04-nodes-and-shells.md) | **how to open a shell on a node** (`ssh` through the login node, or `docker exec`), users, the fidelity VM |
| 5 | [The web page](05-web-page.md) | login, panels, fault actions, scenarios |
| 6 | [Scenarios and faults](06-scenarios.md) | play, fast-forward, inject by hand, write your own |
| 7 | [The governor](07-governor.md) | memory protection, levels, what gets paused or stopped |
| 8 | [Configuration](08-configuration.md) | `sim.yaml`, profiles, changing the topology |
| 9 | [Troubleshooting](09-troubleshooting.md) | symptoms, causes, fixes, the diagnostics bundle |
| 10 | [Reference](10-reference.md) | every command, script, port, file and environment variable |
| 11 | [Which command do I use?](11-which-command.md) | `opti` or `simctl`, which profile, what to run for each goal |
| 12 | [Glossary](12-glossary.md) | the words used in the manual and the web page |

The same pages open inside the web page: click **? Help** in its header (http://127.0.0.1:8088/help/), with a sidebar and search.

Screenshots of the web page and of real terminal output are in chapters 3, 4 and 5 (`img/`).

Short on time? [Getting started](../guides/getting-started.md) is the 10-minute version. Importing the whole cluster into a monitoring client? See [export a profile](../guides/export-profile.md). Connecting one component at a time (Grafana, Jira, SSH...)? See [client setup by component](../guides/client-setup-by-component.md). Testing a client's SSH handling? Follow [SSH connection testing](../guides/ssh-connection-testing.md) (with screenshots).

## Conventions

- Commands run from the repository root. `./scripts/benchctl <cmd>` is `./scripts/benchctl <cmd>` without
  activating the virtualenv.
- Nothing in the normal start, use and stop path needs `sudo` (ADR 0010).
- "Node" means a simulated node: a container (a compute or GPU node running `slurmd`), not a machine.
- Parts that are not built yet are marked **not built**; [PROGRESS.md](../PROGRESS.md) is the live list.
