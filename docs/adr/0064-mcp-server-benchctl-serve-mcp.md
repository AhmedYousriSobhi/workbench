# ADR 0064: An MCP server: `benchctl serve mcp`

Status: proposed, waiting for the owner's approval (issue 603). Nothing here is built. Decision A-4 of the agent interface.

## Context
Typed tools let an agent act without reading documentation, and its client shows the tool list for free.

## Decision
- `benchctl serve mcp` speaks MCP over stdio. It wraps the same code paths as the CLI and goes through the governor exactly as the CLI does; it adds no new privilege.
- Built on the official Python MCP SDK as an optional extra (`pip install -e '.[mcp]'`), imported lazily inside the `serve mcp` command and never by the web UI process, so the web page's 50 MB budget (SPEC W-1) and the no-pydantic rule (ADR 0008) are untouched. A test checks that the web UI never imports the package.
- Tools (names stable): `workbench_describe`, `workbench_plan`, `workbench_up`, `workbench_down`, `scenario_list`, `scenario_run`, `scenario_status`, `fault_inject`, `fault_clear`, `traffic_get`, `capture_start`, `capture_read`, `capture_stop`, `alerts_get`, `wait`, `report_export`. Each has a one or two sentence description and typed inputs. `AGENTS.md`, the recipes and the schemas are also MCP **resources**.
- Each read tool returns the same data as its `--json` twin; a test compares them.

## Consequences
- A new optional dependency and a new long-running process, only when someone starts it.
- Open question for the owner: the SDK pulls in pydantic. That is acceptable for an optional extra loaded only by `serve mcp`; please confirm.
- Open question for the owner: `report_export` and `alerts_get` need pieces that do not exist yet (`export report`, a Prometheus alerts read). Build them in this program, or ship the MCP server without those two tools first?
