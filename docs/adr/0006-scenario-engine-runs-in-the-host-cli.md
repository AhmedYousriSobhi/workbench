# ADR 0006: the scenario engine runs in the host CLI, not in a container

Status: accepted (default; revisit with the Web UI)

## Context
SPEC C-9 lists a `scenario-engine` component (P5 container). Its steps must reach the mocks' `/sim/inject`
endpoints and drain Slurm nodes (`scontrol` inside slurmctld). A container would need the Docker socket for the
second part, which the project forbids (CLAUDE.md: containers stay unprivileged, no Docker socket).

## Decision
The engine is `benchctl/scenario.py`, run by `benchctl scenario`. It talks HTTP to the localhost-published inject
ports and uses `docker exec` for `scontrol`, like the rest of `benchctl`. No new container, no new privilege.
The `scenario-engine` entry in the compose catalog stays unbuilt (skipped by `up`).

## Addendum: the placeholders are gone (issue 473)
The `scenario-engine`, `user-sim` and `ticket-bot` catalog entries, their empty `components/` folders, the `scenarios` and `load` parts
and the `--simulation` option are removed; so is the `ticket-bot` scenario target (`gpu-xid-storm` no longer has a ticket-bot step).
Load is the login node's `user_sim.py` (`benchctl load on|off`). Addresses 13 to 15 stay reserved and unused. A future ticket bot runs
on the login node and gets its own issue. Profile budgets fell by the 60 MB the engine's cap used to count.

## Addendum: personas, failures and a daily rhythm (issue 680)
`user_sim.py` gets three opt-in behaviours, all inside the one process on the login node (no container, no extra memory): `--personas`
(the `persona:` label of each `sim.yaml` user picks a job mix: GPU share, GPUs, CPUs, length, a peak hour; some jobs end with exit 1 and some
are `scancel`led by their owner), and `--day SECONDS` (a pure `rhythm(hour, peak)` multiplies the arrival rate, one simulated day lasting
SECONDS real seconds; `benchctl load on --personas --day 600`). Without them nothing changes. The 20-pending cap stays, the cancel list is
capped at 40, and the governor's L1 flag still pauses arrivals. The mix, rhythm and plan are pure functions of a seeded RNG and the hour, so
unit tests need no cluster. A persona unknown to the table gets the default mix. The ticket bot is a separate issue (#681).

## Consequences
- The Web UI (W-3: governor socket only) cannot run the engine itself. Options when the UI is built: the UI
  backend runs scenarios in-process as the unprivileged `workbench` user (only the HTTP targets), or the governor
  gains a narrow `scenario` socket command. Decided in the UI issue.
- Slurm steps need a user in the `docker` group, same as `benchctl up`.

## Revisit when
Steps need something only a long-running service can do (schedules, concurrent scenarios).

## Addendum: user simulation scope (issue 683)
Decided 2026-11-14: personas, failure behaviour and a daily rhythm run in the existing simulator process (no new
container, same concurrency cap), tracked in issue 680. The ticket bot is a separate issue, 681. Neither is built yet.
