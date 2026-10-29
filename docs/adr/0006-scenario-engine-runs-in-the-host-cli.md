# ADR 0006: the scenario engine runs in the host CLI, not in a container

Status: accepted (default; revisit with the Web UI)

## Context
SPEC C-9 lists a `scenario-engine` component (P5 container). Its steps must reach the mocks' `/sim/inject`
endpoints and drain Slurm nodes (`scontrol` inside slurmctld). A container would need the Docker socket for the
second part, which the project forbids (CLAUDE.md: containers stay unprivileged, no Docker socket).

## Decision
The engine is `simctl/scenario.py`, run by `simctl scenario`. It talks HTTP to the localhost-published inject
ports and uses `docker exec` for `scontrol`, like the rest of `simctl`. No new container, no new privilege.
The `scenario-engine` entry in the compose catalog stays unbuilt (skipped by `up`).

## Consequences
- The Web UI (W-3: governor socket only) cannot run the engine itself. Options when the UI is built: the UI
  backend runs scenarios in-process as the unprivileged `hpcsim` user (only the HTTP targets), or the governor
  gains a narrow `scenario` socket command. Decided in the UI issue.
- Slurm steps need a user in the `docker` group, same as `simctl up`.

## Revisit when
Steps need something only a long-running service can do (schedules, concurrent scenarios).
