# ADR 0071: Default alert rules over the simulated exporter

Status: accepted by the owner on 2026-11-14 (issue 633). Built.

## Context
Prometheus shipped with no rules, so `get alerts` and `wait alert` (ADR 0066) always read an empty list and the `gpu-fault-to-alert` recipe could not end in an alert.

## Decision
- `observability/rules.yml` is mounted into Prometheus (`rule_files`, 15 s evaluation) with three rules: `GpuXid` (page), `GpuOverheat` (warning) and `NodeDown` (page).
- They read the simulated DCGM exporter only. `GpuOverheat` follows the exporter's temperature, so a BMC temperature step does not fire it. `NodeDown` applies to GPU nodes only (compute nodes publish no exporter series) and resolves by itself after 3 minutes.
- The app under test keeps its own alerting; these rules exist so the read side of the agent interface has something to read.

## Consequences
- A small file the owner can extend; `tests/test_compose.py` checks it is loaded and `promtool check rules` passes.
- Not every injected fault raises an alert (for example `gpu-xid-storm` does not fire `GpuOverheat`); the recipe says which rule each fault fires.
