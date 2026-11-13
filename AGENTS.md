# AGENTS.md

You are an AI agent that wants to **use** workbench as a test target. (To **change** this repository, read `CLAUDE.md` instead.)

workbench is a pretend HPC cluster for one Ubuntu laptop: real Slurm in small containers, fake BMCs, GPUs, Jira and BCM, Prometheus and Grafana, named faults to trigger, and a memory governor that refuses to start what will not fit. Test your app against it; never fit it to one app.

## Start (no sudo)

```bash
./scripts/benchctl plan --json            # will it fit? a REFUSED is a decision, not a bug
./scripts/benchctl start                  # builds on first run; prints a link
./scripts/benchctl describe workbench --json   # state, endpoints, faults, scenarios, next steps
```

## Endpoints

<!-- BEGIN endpoints -->
| Id | Address | Credential (a file, never printed) |
|---|---|---|
| login | `10.141.255.254:22` | `compose/generated/secrets/ssh/id_ed25519` |
| nodes | `10.141.0.x (compute), 10.141.100.x (GPU)` | `compose/generated/secrets/ssh/id_ed25519` |
| bmc | `https://10.141.255.12:8443/redfish/v1` | `compose/generated/secrets/bmc_credentials` |
| bcm | `http://10.141.255.10:8081/json` | none |
| jira | `http://10.141.255.11:8080` | none |
| slurm | `http://10.141.255.2:6820` | none |
| gpu | `http://10.141.255.22:9400/metrics` | none |
<!-- END endpoints -->

Optional parts (Teleport, jump host, Kubernetes, real Jira): `benchctl get endpoints --json`.

## The ten verbs

<!-- BEGIN verbs -->
| Command | What it does |
|---|---|
| `benchctl start` | Check the machine, build, start the cluster and the web page |
| `benchctl stop` | Stop the web page, the cluster and the governor this tool started |
| `benchctl plan` | Estimate the memory a workbench needs against what the host has free |
| `benchctl describe workbench` | One call for situational awareness |
| `benchctl get faults` | What is wrong right now |
| `benchctl get endpoints` | Where a client connects, and which file holds each credential |
| `benchctl get traffic` | Who called which mock, how it ended and how long it took |
| `benchctl scenario run` | Play a scenario against the running stack |
| `benchctl wait ready` | Until every started service answers (the same check `start` uses) |
| `benchctl export profile` | Write a profile file the monitoring client can import |
<!-- END verbs -->

MCP instead of the shell: `pip install -e '.[mcp]'`, then run `benchctl serve mcp` (stdio). Its tools run these same verbs; AGENTS.md, the recipes and the schemas are its resources.

Add `--json` to any read verb. Scenarios: `benchctl scenario list`. Recipes (one goal each, exact commands): `docs/agents/recipes/`.

## Rules

- No sudo. Never edit `compose/generated/`. Never print or copy a credential: use the file path.
- Stop what you started: `benchctl stop`.
- `REFUSED` means the governor protected the machine: free memory or pick a smaller profile (`benchctl plan`). Do not retry in a loop.
- Block with `benchctl wait ...` instead of polling. Destructive verbs need `--yes` when `BENCHCTL_AGENT=1`.
- Set `BENCHCTL_AGENT=1`: one-line errors `WB-KIND-DETAIL: message | fix: COMMAND`, no colour, logs in files.
- Do not name or imitate the monitoring client in files you commit.

<!-- BEGIN contract -->
JSON contract version 1.3. Schemas: `docs/agents/schemas/`. Changes: `docs/agents/CHANGELOG.md`. Error codes: `docs/agents/ERROR-CODES.md`.
<!-- END contract -->
