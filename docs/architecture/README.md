# Architecture

The deep design of opti-sim. The [project README](../../README.md) has the one-page overview; [SPEC.md](../../SPEC.md)
is the normative requirement list and wins over these pages if they disagree. Section numbers are kept from the
original single README, so references such as "section 6.3" still mean the same thing.

| Page | Sections | Read it for |
|---|---|---|
| [01 Problem and design principles](01-problem-and-principles.md) | 1, 2 | Why 16 GB breaks the old lab; the nine principles |
| [02 Architecture overview](02-architecture.md) | 3 | The full component diagram by priority slice |
| [03 Component decisions](03-components.md) | 4 | Slurm, Kubernetes, BMC, BCM, Jira, observability, workload sims, fidelity VM, storage/network/time |
| [04 Memory contract and profiles](04-memory-and-profiles.md) | 5 | Budgets per component, the `min`/`std`/`full` profiles, the pre-flight |
| [05 The governor](05-governor.md) | 6 | Placement, signals, escalation ladder L0-L5, behaviour rules, host tuning |
| [06 Control, Web UI and scenarios](06-ux-and-scenarios.md) | 7 | `simctl` commands, the web page, scenario format and catalog |
| [07 macOS testing](07-macos.md) | 8 | Why macOS is an external agent only |
| [08 Repository layout and migration](08-repository-and-migration.md) | 9, 10 | Where things live; how the old Vagrant lab maps over |
| [09 Roadmap, open decisions, risks](09-roadmap-decisions-risks.md) | 11, 12, 13 | Phases and exit tests; decisions still open; risks |
| [10 References](10-references.md) | 14 | Sources researched |
| [11 The governor as built](11-governor-as-built.md) | none (derived from code) | Boot order, control cycle, lock hold time as measured, follow-up candidates |

Related: [decision records](../adr/), [build progress](../PROGRESS.md), [user guide](../guides/getting-started.md).
