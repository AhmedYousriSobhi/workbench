# ADR 0066: Waiting is a verb: `benchctl wait`

Status: accepted by the owner on 2026-11-13 (issue 603, decisions in issue 613). Nothing here is built yet. Decision A-7 of the agent interface.

## Context
Agents poll in a loop, spending a call and tokens per poll, and each poll risks hammering the governor.

## Decision
- `benchctl wait ready [--timeout S]`, `wait scenario NAME`, `wait alert NAME [--firing|--resolved]`, `wait node-state NODE STATE`. It blocks, then returns one JSON result saying what it saw. Exit 0 on success, 1 on timeout, 2 when a precondition is not met.
- It polls internally at a fixed gentle interval and reads only what the corresponding read verbs read (no extra Docker call from the web process).
- A new CLI verb, so SPEC section 7 lists it.

## Consequences
- Agents make one call per wait.
- Decided: `wait alert` works only when the Prometheus part runs. Otherwise it fails with its own exit code, 2 (precondition not met), separate from timeout (1), and says in one line which command enables Prometheus.
