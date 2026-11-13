# ADR 0061: `benchctl describe workbench --json`: one call for situational awareness

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Nothing here is built yet. Decision A-2 of the agent interface.

## Context
An agent that has just started needs to know what is running, which endpoints to call, which faults and scenarios exist and what to do next. Today that takes several commands (`status`, `get nodes`, `get workbench`, `get scenarios`, the connections table) and parsing of each.

## Decision
- `describe` gains the resource `workbench` (ADR 0015 grammar; `describe node` exists today, `get workbench` shows the saved selection). `benchctl describe workbench --json` returns one compact object: `contract_version`, `state` (running, profile, governor level, available memory), `clusters` with pools, `endpoints` (service, URL, auth kind and a **pointer** to the credential), `faults_active`, `scenarios` (name, summary, minutes), `fault_targets` and `next`.
- No secret is ever a field value. A credential is a command (`benchctl get token`) or a gitignored file path.
- Size: at most 6,000 characters for the default profile, checked by a test.
- It reads the same sources the web page's Connections view and the governor's read-only commands read; it adds no new access path and no Docker call from the web UI process (SPEC W-3).

## Consequences
- An agent's first call after `start` is one JSON object of about 1,500 tokens.
- The endpoint list reuses the Connections registry, so there is one list of endpoints, not two.
