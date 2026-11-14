# ADR 0066: Waiting is a verb: `benchctl wait`

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Nothing here is built yet. Decision A-7 of the agent interface.

## Context
Agents poll in a loop, spending a call and tokens per poll, and each poll risks hammering the governor.

## Decision
- `benchctl wait ready [--timeout S]`, `wait scenario NAME`, `wait alert NAME [--firing|--resolved]`, `wait node-state NODE STATE`. It blocks, then returns one JSON result saying what it saw. Exit 0 on success, 1 on timeout, 2 when a precondition is not met.
- It polls internally at a fixed gentle interval and reads only what the corresponding read verbs read (no extra Docker call from the web process).
- A new CLI verb, so SPEC section 7 lists it.

## Addendum (2026-11-13, built in issue 625)
`wait scenario NAME` follows the web page's run state (`/api/scenarios/run`, with the page's token read from the host's secrets file), because a `scenario run` in a terminal blocks until it ends by itself. A scenario that ended failed returns outcome `failed` (exit 1). Without the page it is a precondition failure (exit 2). `wait node-state` compares the container state `get nodes` shows. `wait alert` reads `GET /api/v1/alerts` of the workbench's Prometheus, which ships no alert rules: it returns once rules are loaded and one fires.

## Addendum (2026-11-14, issue 633)
Decided by the owner: the workbench ships a small default rule file, `observability/rules.yml`, mounted into Prometheus (`rule_files`, 15 s evaluation). `GpuXid` (any GPU with `DCGM_FI_DEV_XID_ERRORS` above 0 for 15 s, severity page), `GpuOverheat` (`DCGM_FI_DEV_GPU_TEMP` above 85 C for 15 s, warning; it reads the exporter, which a BMC temperature step does not change) and `NodeDown` (a node whose series existed 3 minutes ago and are gone now, severity page; it resolves by itself once the 3 minutes pass, because a powered-off node publishes nothing). They watch the simulated exporter only; the app under test keeps its own alerting. `get alerts` and `wait alert` therefore have something to read, and the `gpu-fault-to-alert` recipe can end in `wait alert GpuXid`.

## Consequences
- Agents make one call per wait.
- Decided: `wait alert` works only when the Prometheus part runs. Otherwise it fails with its own exit code, 2 (precondition not met), separate from timeout (1), and says in one line which command enables Prometheus.
