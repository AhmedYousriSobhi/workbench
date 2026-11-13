# ADR 0064: An MCP server: `benchctl serve mcp`

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Nothing here is built yet. Decision A-4 of the agent interface.

## Context
Typed tools let an agent act without reading documentation, and its client shows the tool list for free.

## Decision
- `benchctl serve mcp` speaks MCP over stdio. It wraps the same code paths as the CLI and goes through the governor exactly as the CLI does; it adds no new privilege.
- Built on the official Python MCP SDK as an optional extra (`pip install -e '.[mcp]'`), imported lazily inside the `serve mcp` command and never by the web UI process, so the web page's 50 MB budget (SPEC W-1) and the no-pydantic rule (ADR 0008) are untouched. A test checks that the web UI never imports the package.
- Tools (names stable): `workbench_describe`, `workbench_plan`, `workbench_up`, `workbench_down`, `scenario_list`, `scenario_run`, `scenario_status`, `fault_inject`, `fault_clear`, `traffic_get`, `capture_start`, `capture_read`, `capture_stop`, `alerts_get`, `wait` (and `report_export` once issue 614 lands). Each has a one or two sentence description and typed inputs. `AGENTS.md`, the recipes and the schemas are also MCP **resources**.
- Each read tool returns the same data as its `--json` twin; a test compares them.

## Consequences
- A new optional dependency and a new long-running process, only when someone starts it.
- Decided: the SDK's pydantic dependency is accepted as the optional `[mcp]` extra. The MCP package is imported only inside `serve mcp`, so tab completion and ordinary CLI calls do not slow down. The import test covers three processes: the web page, the governor and plain `benchctl` startup.
- Decided: `alerts_get` stays. `wait alert` (ADR 0066) must read Prometheus alerts anyway, so step 11 adds `benchctl get alerts --json` on that code and `alerts_get` is a thin wrapper.
- Decided: `report_export` is deferred to its own issue (613's follow-up, issue 614). It stays out of `AGENTS.md` and the tool list until `export report` exists.
