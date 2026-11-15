---
name: workbench
description: Drive workbench, a memory-governed simulated HPC cluster (real Slurm, fake BMCs and GPUs, Jira, BCM, optional Teleport and Kubernetes), as a test target for a monitoring app. Use it to reproduce a cluster fault, watch how the app reacts, see which API calls it makes, or measure its memory.
---

# workbench

You test a client: do not change the lab to fit one client's quirks. Read `AGENTS.md` first (under 2,000 tokens). Then:

1. `./scripts/benchctl plan --json`. `fits: false` or `REFUSED` means the memory governor is protecting the machine: free memory or pick a smaller profile. Never retry in a loop.
2. `./scripts/benchctl start` (add `--with teleport`, `k8s`, `jira-real` or `jump` only when the test needs it), then `./scripts/benchctl wait ready --timeout 180`.
3. `./scripts/benchctl describe workbench --json`: state, endpoints (credentials are file paths, never printed), faults, scenarios, next steps.
4. Pick a recipe in `docs/agents/recipes/` for the goal.
5. `./scripts/benchctl scenario run NAME --json`, then `./scripts/benchctl wait node-state NODE STATE --timeout 90`. Block, never sleep-poll.
6. `./scripts/benchctl get faults --json` (also `get endpoints`, `get traffic`, `get alerts`) to assert what the app should show and what it called.
7. `./scripts/benchctl clear faults`, then `./scripts/benchctl stop`. Stop only what you started.

Rules that save a session:
- One lab at a time. Run `docker ps` and `./scripts/benchctl status` first; a lab someone else started is not yours to stop.
- No sudo. Never edit `compose/generated/`.
- A halted scenario leaves its fault in place: `clear faults` undoes it.
- Run from a clean copy of `main`, not a checkout with uncommitted changes.
- Not exercised, so not an app defect: `docs/guides/client-testing.md` lists them.
- Set `BENCHCTL_AGENT=1` for one-line coded errors (`docs/agents/ERROR-CODES.md`); destructive verbs then need `--yes`. Check `contract_version` against `docs/agents/CHANGELOG.md`.
- Prefer the CLI. `benchctl serve mcp` offers the same verbs as typed tools if it is configured.
- Never name or imitate the monitoring app in committed files.
