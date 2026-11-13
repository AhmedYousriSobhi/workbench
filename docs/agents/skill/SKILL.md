---
name: workbench
description: Use workbench, a memory-governed simulated HPC cluster (Slurm, fake BMCs, GPUs, Jira, BCM), as a test target for a monitoring app. Start it, read its state as JSON, play named faults, wait for results, stop it.
---

# workbench

Read `AGENTS.md` in the workbench repository first (under 2,000 tokens). Then:

1. `./scripts/benchctl plan --json`, then `./scripts/benchctl start`. A `REFUSED` is the memory governor protecting the machine: free memory or pick a smaller profile; do not retry in a loop.
2. `./scripts/benchctl describe workbench --json` for state, endpoints (credentials are file paths, never printed), active faults, scenarios and next steps.
3. Pick a recipe in `docs/agents/recipes/` for the goal (connect your app, node down, GPU fault, drain to ticket, API errors, SSH failures, Kubernetes, what did my app call, write a scenario, load and memory).
4. Play faults with `./scripts/benchctl scenario run NAME --json`; block with `./scripts/benchctl wait ...` instead of polling; stop what you started with `./scripts/benchctl stop`.

Set `BENCHCTL_AGENT=1` for one-line coded errors and `--yes` on destructive verbs. Prefer the MCP server when it is configured: `benchctl serve mcp` (`pip install -e '.[mcp]'`). Never name or imitate the monitoring app in committed files. Error codes: `docs/agents/ERROR-CODES.md`.
