# ADR 0063: Recipes, not prose: short task pages for agents

Status: proposed, waiting for the owner's approval (issue 603). Nothing here is built. Decision A-5 of the agent interface.

## Context
An agent given the manual will read far more than it needs. What it wants is a short, copyable path to one test goal.

## Decision
- `docs/agents/recipes/` holds one page per goal, each at most 60 lines, with the same headings: Goal, Needs (profile and parts), Steps (exact commands with `--json`), What your app should show, Assert (the `expect` or `wait` that proves it), Clean up, Memory cost.
- Minimum set: `connect-your-app`, `gpu-fault-to-alert`, `drain-to-ticket`, `node-down`, `api-errors-and-retries`, `ssh-failures`, `kubernetes-node-notready`, `what-did-my-app-call`, `write-a-scenario`, `load-and-memory`.
- `tests/test_agents_doc.py` checks the length and headings, and that every command in `AGENTS.md` and the recipes exists in the CLI. Three recipes (`gpu-fault-to-alert`, `drain-to-ticket`, `what-did-my-app-call`) also run end to end in `tests/real/test_agent_recipes_real.py`.

## Consequences
- Recipes cannot drift without a test failing.
- Each recipe states its memory cost, so an agent does not start what will not fit.
