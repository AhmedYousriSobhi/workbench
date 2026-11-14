# ADR 0064: An MCP server: `benchctl serve mcp`

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Built (issues 616 to 628, 633 to 639). Decision A-4 of the agent interface.

## Context
Typed tools let an agent act without reading documentation, and its client shows the tool list for free.

## Decision
- `benchctl serve mcp` speaks MCP over stdio. It wraps the same code paths as the CLI and goes through the governor exactly as the CLI does; it adds no new privilege.
- Built on the official Python MCP SDK as an optional extra (`pip install -e '.[mcp]'`), imported lazily inside the `serve mcp` command and never by the web UI process, so the web page's 50 MB budget (SPEC W-1) and the no-pydantic rule (ADR 0008) are untouched. A test checks that the web UI never imports the package.
- Tools (names stable): `workbench_describe`, `workbench_plan`, `workbench_up`, `workbench_down`, `scenario_list`, `scenario_run`, `scenario_status`, `fault_inject`, `fault_clear`, `traffic_get`, `capture_start`, `capture_read`, `capture_stop`, `alerts_get`, `wait` (and `report_export` once issue 614 lands). Each has a one or two sentence description and typed inputs. `AGENTS.md`, the recipes and the schemas are also MCP **resources**.
- Each read tool returns the same data as its `--json` twin; a test compares them.

## Addendum (2026-11-13, built in issue 627)
Built on `mcp` 2.3.0 (`MCPServer`), pinned in the `[mcp]` extra. Every tool runs the matching `benchctl` command in a subprocess with `BENCHCTL_AGENT=1` (no code is duplicated, the governor path and the `--yes` rule apply by construction). Twelve tools: `workbench_describe`, `workbench_plan`, `workbench_up`, `workbench_down`, `scenario_list`, `scenario_run`, `faults_get`, `endpoints_get`, `fault_inject`, `traffic_get`, `alerts_get`, `wait`. Differences from the list above, because a tool needs a CLI verb behind it and these have none:
- `scenario_status`, `fault_clear` and `capture_start|read|stop` are **not built** (built 2026-11-14, see the addendum below). `fault_inject` maps to `set node NODE off|on|down|drain|resume` (the verb that exists); there is no verb that clears every fault, and `get capture` streams NDJSON for a bounded time rather than start, read, stop.
- `faults_get` and `endpoints_get` are added: the `get faults` and `get endpoints` contract outputs exist for them.
- `workbench_down` and `fault_inject` carry the destructive annotation; read tools are read-only. `AGENTS.md`, `docs/agents/recipes/*` and `docs/agents/schemas/*` are resources (`workbench://agents`, `workbench://recipes/NAME`, `workbench://schemas/NAME`).
- `tests/test_mcp_isolation.py` checks that the web page, the governor, plain `benchctl` startup and tab completion load neither `mcp` nor `pydantic`.

## Consequences
- A new optional dependency and a new long-running process, only when someone starts it.
- Decided: the SDK's pydantic dependency is accepted as the optional `[mcp]` extra. The MCP package is imported only inside `serve mcp`, so tab completion and ordinary CLI calls do not slow down. The import test covers three processes: the web page, the governor and plain `benchctl` startup.
- Decided: `alerts_get` stays. `wait alert` (ADR 0066) must read Prometheus alerts anyway, so step 11 adds `benchctl get alerts --json` on that code and `alerts_get` is a thin wrapper.
- Decided: `report_export` is deferred to its own issue (613's follow-up, issue 614). It stays out of `AGENTS.md` and the tool list until `export report` exists.

## Addendum (2026-11-14, issue 635)
The owner chose to build all three missing groups, each with a CLI verb first (ADR 0015 grammar). `scenario_status` runs `benchctl get scenario-status --json` (the web page's run state; exit 1 without the page). `fault_clear` runs `benchctl clear faults --json`: it clears every injected node fault through the scenario engine's `clear` steps (BMC farm for every hardware node, GPU exporter for GPU nodes), so the BCM, Slurm and the metrics follow; faults on a service or a credential keep their own restore steps. `capture_start|read|stop` stay one tool, `capture_once`, because an agent needs one bounded answer: `benchctl get capture NODE --once [--seconds S] [--max-messages N]` (10 s and 200 messages by default, at most 60 s and 2000) returns one object and asks the governor first like a scenario does (refused from L2). Fifteen tools. Contract 1.4; schemas `get-scenario-status`, `clear-faults`, `get-capture-once`. `scenario_status` and `capture_once` are read-only; `fault_clear` is a write, not destructive (it restores). Code: `benchctl/agentverbs.py`.
