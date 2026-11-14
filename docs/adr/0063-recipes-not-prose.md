# ADR 0063: Recipes, not prose: short task pages for agents

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Nothing here is built yet. Decision A-5 of the agent interface.

## Context
An agent given the manual will read far more than it needs. What it wants is a short, copyable path to one test goal.

## Decision
- `docs/agents/recipes/` holds one page per goal, each at most 60 lines, with the same headings: Goal, Needs (profile and parts), Steps (exact commands with `--json`), What your app should show, Assert (the `expect` or `wait` that proves it), Clean up, Memory cost.
- Minimum set: `connect-your-app`, `gpu-fault-to-alert`, `drain-to-ticket`, `node-down`, `api-errors-and-retries`, `ssh-failures`, `kubernetes-node-notready`, `what-did-my-app-call`, `write-a-scenario`, `load-and-memory`.
- `tests/test_agents_doc.py` checks the length and headings, and that every command in `AGENTS.md` and the recipes exists in the CLI. Three recipes (`gpu-fault-to-alert`, `drain-to-ticket`, `what-did-my-app-call`) also run end to end in `tests/real/test_agent_recipes_real.py`.

## Addendum (2026-11-13, built in issue 626)
The ten recipes are written and their commands and flags are checked by `tests/test_agents_doc.py`. The end-to-end `tests/real/test_agent_recipes_real.py` is not written yet: `gpu-fault-to-alert` ends in an alert, and the workbench ships no Prometheus alert rules (see ADR 0066 addendum), so that recipe waits for a decision on a default rule file. Decided 2026-11-14 (issue 633): the default rules exist (ADR 0066 addendum), so the recipe asserts `wait alert GpuXid`.

## Consequences
- Recipes cannot drift without a test failing.
- Each recipe states its memory cost, so an agent does not start what will not fit.
