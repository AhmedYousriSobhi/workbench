# workbench

workbench is a simulated HPC and AI cluster that runs on one 16 GB Ubuntu workstation. Point your monitoring client at it, break nodes, drain them, expire certificates and rate-limit Jira on command, and watch whether the client notices. Slurm is real; the BMCs, BCM, Jira and GPU metrics are faithful stand-ins; a memory governor outside the cluster keeps your machine usable.

New here? Follow the three steps below, or read [The Tour](00-tour.md) (five minutes, one picture per step). Want to do one thing, such as break a node or test tickets? Pick a [tutorial](../tutorials/README.md). Short on time, or want the commands only? [Getting started](../guides/getting-started.md) is the 10-minute version.

Where the documents disagree, [SPEC.md](../../SPEC.md) wins. [The architecture](../architecture/README.md) explains why it is built this way, and [what works today](../PROGRESS.md) lists what is built.

| # | Chapter | You will learn |
|---|---|---|
| 0 | [The Tour](00-tour.md) | eight stops, five minutes: start it, look, break a node, watch it heal |
| 1 | [What workbench is](01-what-it-is.md) | what is real and what is faked, the parts, the profiles, the ports |
| 2 | [Install and start](02-install-and-start.md) | prerequisites, first start, stop, status |
| 3 | [Using the cluster](03-using-the-cluster.md) | up, scale, down, Slurm jobs, the mock services, Grafana |
| 4 | [Nodes and shells](04-nodes-and-shells.md) | **how to open a shell on a node** (`ssh` through the login node, or `docker exec`), users, the fidelity VM |
| 5 | [The web page](05-web-page.md) | login, layout, the entity list, the Infrastructure architecture, Teleport, node actions, plan, scenarios |
| 6 | [Scenarios and faults](06-scenarios.md) | play, fast-forward, inject by hand, write your own |
| 7 | [The governor](07-governor.md) | memory protection, levels, what gets paused or stopped |
| 8 | [Configuration](08-configuration.md) | `sim.yaml`, profiles, changing the topology |
| 9 | [Troubleshooting](09-troubleshooting.md) | symptoms, causes, fixes, the diagnostics bundle |
| 10 | [Reference](10-reference.md) | every command, script, port, file and environment variable |
| 11 | [Which command do I use?](11-which-command.md) | which command, which profile, what to run for each goal |
| 12 | [Glossary](12-glossary.md) | the words used in the manual and the web page |

The same pages open inside the web page: click **? Help** in its header (http://127.0.0.1:8088/help/), with a sidebar and search. Screenshots of the web page and of real terminal output are in chapters 3, 4 and 5.

## Find your goal

The [tutorials](../tutorials/README.md) walk through each goal below step by step.

| I want to... | Read |
|---|---|
| test my monitoring client against a cluster | [Testing a client against workbench](../guides/client-testing.md) |
| fill in a run-sheet while I test the client, one scenario per row | [Client test worksheet](../guides/client-test-worksheet.md) |
| see what a cluster costs before I start it | [Choose the workbench](../guides/choose-workbench.md) |
| import the whole cluster into the client in one step | [Export a profile](../guides/export-profile.md) |
| connect one component at a time (Grafana, Jira, SSH, ...) | [Client setup by component](../guides/client-setup-by-component.md) |
| test the client's SSH handling (hung, down, changed host key) | [SSH connection testing](../guides/ssh-connection-testing.md) |
| test tickets: create, note, change status, a node that drains and gets a ticket | [Tickets and their scenarios](../guides/jira-tickets.md) |
| test what a running job does when `slurmd` or `slurmctld` restarts | [Slurm resilience](../guides/slurm-resilience.md) |
| change what the node health check looks at | [NHC checks](../guides/nhc-checks.md) |
| fix Teleport's `unknown certificate authority`, or start a fresh Teleport cluster | [Keep or reset the Teleport cluster](04-nodes-and-shells.md#keep-or-reset-the-teleport-cluster) |
| draw on a diagram canvas (colours, fonts, wires) | [Web UI canvas](../guides/web-ui-canvas.md) |
| work through the backlog | [Issue triage](../guides/issue-triage.md) |

## Conventions

- Commands run from the repository root. `./scripts/benchctl <cmd>` runs benchctl without activating the virtualenv.
- Nothing in the normal start, use and stop path needs `sudo`.
- "Node" means a simulated node: a container (a compute or GPU node running `slurmd`), not a machine.
- A part that is not built yet is marked **not built**; [PROGRESS.md](../PROGRESS.md) is the live list.
