# ADR 0066: Waiting is a verb: `benchctl wait`

Status: proposed, waiting for the owner's approval (issue 603). Nothing here is built. Decision A-7 of the agent interface.

## Context
Agents poll in a loop, spending a call and tokens per poll, and each poll risks hammering the governor.

## Decision
- `benchctl wait ready [--timeout S]`, `wait scenario NAME`, `wait alert NAME [--firing|--resolved]`, `wait node-state NODE STATE`. It blocks, then returns one JSON result saying what it saw. Exit 0 on success, 1 on timeout.
- It polls internally at a fixed gentle interval and reads only what the corresponding read verbs read (no extra Docker call from the web process).
- A new CLI verb, so SPEC section 7 lists it.

## Consequences
- Agents make one call per wait.
- Open question for the owner: `wait alert` needs Prometheus's alerts; confirm it is only available when the Prometheus part runs, and says so in one line otherwise.
