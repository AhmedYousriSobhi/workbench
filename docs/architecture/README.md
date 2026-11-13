<p align="center"><img src="../assets/art/arch-README-hero.svg" alt="Architecture: How it works. manual, guides, architecture, ADRs." width="720"></p>

# Architecture

The deep design of workbench. The [project README](../../README.md) has the one-page overview; [SPEC.md](../../SPEC.md)
is the normative requirement list and wins over these pages if they disagree. Section numbers are kept from the
original single README, so references such as "section 6.3" still mean the same thing.

## How it works

Every manual chapter and guide has an **Under the hood** section that says how the thing works and cites its `Source:` (code, SPEC or an ADR). This is the one list of them; the pages below it are the design.

| Topic | In the | Decisions behind it |
|---|---|---|
| [The Tour](../manual/00-tour.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [What workbench is](../manual/01-what-it-is.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [Install and start](../manual/02-install-and-start.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [Using the cluster](../manual/03-using-the-cluster.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [Nodes and shells](../manual/04-nodes-and-shells.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [The web page](../manual/05-web-page.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [Scenarios and faults](../manual/06-scenarios.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [The governor](../manual/07-governor.md#under-the-hood) | manual | [ADR 0010](../adr/0010-rootless-governor-mode.md), [ADR 0059](../adr/0059-governor-signal-model.md) |
| [Configuration](../manual/08-configuration.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [Troubleshooting](../manual/09-troubleshooting.md#under-the-hood) | manual | SPEC and code (see its Source line) |
| [Choose the workbench to build](../guides/choose-workbench.md#under-the-hood) | guides | [ADR 0016](../adr/0016-workbench-selection-and-effective-sim.md), [ADR 0017](../adr/0017-apply-a-plan-through-the-governor.md), [ADR 0021](../adr/0021-parts-by-category.md) |
| [Setting up the monitoring client, one component at a time](../guides/client-setup-by-component.md#under-the-hood) | guides | [ADR 0018](../adr/0018-fixed-addressing-plan.md) |
| [Testing a monitoring client against workbench](../guides/client-testing.md#under-the-hood) | guides | [ADR 0018](../adr/0018-fixed-addressing-plan.md) |
| [Jira contract: compare jira-mock with real Jira Cloud](../guides/jira-contract.md#under-the-hood) | guides | [ADR 0005](../adr/0005-bcm-and-jira-mock-surface.md) |
| [Real Jira (optional)](../guides/jira-real.md#under-the-hood) | guides | [ADR 0026](../adr/0026-real-jira-data-center-as-an-optional-part.md) |
| [Tickets: create, note, edit, change status, and scenarios for them](../guides/jira-tickets.md#under-the-hood) | guides | [ADR 0005](../adr/0005-bcm-and-jira-mock-surface.md) |
| [How much memory the workbench uses](../guides/memory.md#under-the-hood) | guides | SPEC and code (see its Source line) |
| [NHC checks: add, change and remove what the node health check looks at](../guides/nhc-checks.md#under-the-hood) | guides | [ADR 0019](../adr/0019-nhc-checks-are-files-managed-three-ways.md) |
| [Power save and reboot through the BMC farm](../guides/power-and-reboot.md#under-the-hood) | guides | [ADR 0054](../adr/0054-slurm-power-save-and-reboot-through-the-bmc-farm.md) |
| [QOS, priority and preemption](../guides/qos-and-preemption.md#under-the-hood) | guides | [ADR 0055](../adr/0055-real-qos-priority-and-preemption-from-sim-yaml.md) |
| [Slurm resilience: what a running job does when the daemons change under it](../guides/slurm-resilience.md#under-the-hood) | guides | [ADR 0025](../adr/0025-slurm-is-real-the-rest-is-simulated.md), [ADR 0002](../adr/0002-slurm-from-source-no-extra-privileges.md) |
| [Testing SSH connections step by step](../guides/ssh-connection-testing.md#under-the-hood) | guides | [ADR 0011](../adr/0011-ssh-login-node.md), [ADR 0014](../adr/0014-sshd-on-compute-nodes.md), [ADR 0023](../adr/0023-login-nodes-and-their-auth.md) |
| [Slurm topology and a switch going down](../guides/topology.md#under-the-hood) | guides | [ADR 0053](../adr/0053-slurm-topology-from-sim-yaml.md) |
| [Web UI: drawing on a canvas (tokens and wires)](../guides/web-ui-canvas.md#under-the-hood) | guides | [ADR 0038](../adr/0038-redesign-canvas-tokens-and-wire.md), [ADR 0041](../adr/0041-per-request-traffic-record.md), [ADR 0042](../adr/0042-on-demand-wire-capture.md) |

## The design pages

| Page | Sections | Read it for |
|---|---|---|
| [01 Problem and design principles](01-problem-and-principles.md) | 1, 2 | Why 16 GB breaks the old lab; the nine principles |
| [02 Architecture overview](02-architecture.md) | 3 | The full component diagram by priority slice |
| [03 Component decisions](03-components.md) | 4 | Slurm, Kubernetes, BMC, BCM, Jira, observability, workload sims, fidelity VM, storage/network/time |
| [04 Memory contract and profiles](04-memory-and-profiles.md) | 5 | Budgets per component, the `min`/`std`/`full` profiles, the pre-flight |
| [05 The governor](05-governor.md) | 6 | Placement, signals, escalation ladder L0-L5, behaviour rules, host tuning |
| [06 Control, Web UI and scenarios](06-ux-and-scenarios.md) | 7 | `benchctl` commands, the web page, scenario format and catalog |
| [07 macOS testing](07-macos.md) | 8 | Why macOS is an external agent only |
| [08 Repository layout and migration](08-repository-and-migration.md) | 9, 10 | Where things live; how the old Vagrant lab maps over |
| [09 Roadmap, open decisions, risks](09-roadmap-decisions-risks.md) | 11, 12, 13 | Phases and exit tests; decisions still open; risks |
| [10 References](10-references.md) | 14 | Sources researched |
| [11 The governor as built](11-governor-as-built.md) | none (derived from code) | Boot order, control cycle, lock hold time as measured, follow-up candidates |

Related: [decision records](../adr/), [build progress](../PROGRESS.md), [user guide](../guides/getting-started.md).
