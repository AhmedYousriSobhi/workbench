# 0009. Traffic dots come from the mocks' request counters

Status: accepted

## Context
Issue #37 asks for dots that move between blocks to show workload. The Web UI cannot read Slurm (no Docker access,
SPEC W-3), and user-sim does not exist yet, so Slurm job events are not available to it.

## Decision
- Each mock counts the client requests it serves at `GET /sim/stats`. Health checks, `/sim/*` control calls and
  requests carrying `X-Opti-Probe` (the UI's own polling) are not counted, so drawn traffic is traffic from the app,
  Prometheus, or `curl`, not the page watching itself.
- `webui/flow.py` polls the counters (rate-limited, shared by all open tabs) and turns a rise into an event
  `clients -> service`. Scenario steps and fault buttons add `injector -> service` events. Nothing is invented.
- Delivery is SSE at `/api/flow`, opened by the page only while the page is visible, so it costs nothing
  elsewhere. A new stream starts from "now"; old events are not replayed.
- The animation stops at governor level L3 and above (the server also stops polling), when the user pauses it, and
  when the system asks for reduced motion. Counts and states keep updating without animation.
- Round dots and diamonds distinguish the two sources, so meaning never depends on colour alone.

## Consequences
- Slurm job dots (submit to slurmctld to node) wait for either user-sim reporting its submissions or a read-only
  scheduler feed the UI is allowed to use. Until then the page says what it does not show.
- Counters live in memory in each mock and reset when it restarts; the feed re-baselines instead of showing a burst.
- `benchctl up` now builds images (`up --build`), because a stale image would silently lack `/sim/stats`.
