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

## Addendum: the ticket bot (issue 681)
The ticket bot is a second small script on the login node, `images/login/ticket_bot.py`, started by `benchctl load on --tickets` the way the demo client is (copied in with `docker cp`, so no rebuild; no container, no extra memory beyond a stdlib process). It talks only to jira-mock over REST (comments, transitions, assignee, search; no new endpoint), never to a real Jira. It is a separate process from `user_sim.py` because it does no Slurm work and must be switchable alone. Decisions are a pure `Bot.step` over a seeded RNG, so tests need no cluster; the rhythm curve is the same as the simulator's (a test keeps them equal). Flows and rate (assign, start, comment, resolve after N seconds, some unassigned, some reopened; one action per 120 s) are the research recommendation, not yet confirmed by the owner, and are settings.

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

## Addendum: node selectors, a fault model, expect coverage (issue 470)
Decided 2026-11-14. Three changes to the scenario language, all inside `benchctl/scenario.py` and two small modules, no new process:
- **Selectors** (`benchctl/selectors.py`): a scenario names nodes as `first gpu node`, `second cpu node`, `any 2 gpu nodes`, `all gpu nodes` (`gpu`, `cpu`,
  `k8s gpu`, `k8s cpu`), alone in a list or in braces inside a text. They are resolved against `sim.yaml` when the CLI or the page loads the scenario
  (`scenario.load(name, sim=...)`); "any N" is the first N, so runs repeat. Too few nodes of a kind is an `invalid:` error from `--dry-run`. A test fails
  when a shipped scenario writes a node name in `steps` or `expect`; free text such as `description` may. A selector is resolved only as a whole value or in braces; a bare phrase inside a longer string (a Jira `jql`) is sent literally, so write `labels = {second cpu node}` (resolving free text would turn prose into node names); `test_no_selector_phrase_survives_resolution_in_any_shipped_scenario` fails when one remains after resolution on the default, 2-cpu and 0-gpu clusters (issue 772).
- **Fault model** (`benchctl/faultmodel.py`): a `fault` step (`inject: NAME` or `clear: NAME`, `nodes`) expands in `plan()` into the physical effects of one
  fault (exporter and DCGM, Redfish event log, BMC), so a scenario injects the root cause and asserts what the cluster does (NHC drains, `node_watch` and the
  engine's fault side effects file the ticket) instead of scripting the reaction. The expansion yields ordinary steps, so the timeline, the page preview and
  the side effects need no change. Shipped: `gpu-fell-off-bus`, `fan-failure`, `ib-port-down`, `nic-flap`. `gpu-xid-storm` now injects only the cause and asserts
  both drains and the ticket. The BCM mock has no health-check state, so no fault writes there.
- **Expect coverage**: `fan-failure` and `gpu-xid-storm` assert the ticket the fault files (and the drains, for the GPU fault). Scenarios with no `expect` stay listed in
  `tests/test_scenario.py` (`NO_EXPECT`, with a reason each, a list that can only shrink), among them faults felt by the client only (Jira and SSH faults, expired
  certificates and tokens) and `preempt-burst` and `reboot-asap`, whose observable state the owner has not yet named (issue 732).
