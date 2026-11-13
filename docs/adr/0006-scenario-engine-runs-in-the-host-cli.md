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

## Consequences
- The Web UI (W-3: governor socket only) cannot run the engine itself. Options when the UI is built: the UI
  backend runs scenarios in-process as the unprivileged `workbench` user (only the HTTP targets), or the governor
  gains a narrow `scenario` socket command. Decided in the UI issue.
- Slurm steps need a user in the `docker` group, same as `benchctl up`.

## Revisit when
Steps need something only a long-running service can do (schedules, concurrent scenarios).
