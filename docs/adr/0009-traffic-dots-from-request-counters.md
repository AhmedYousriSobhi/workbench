# 0009. Traffic dots come from the mocks' request counters

Status: accepted

## Context
Issue #37 asks for dots that move between blocks to show workload. The Web UI cannot read Slurm (no Docker access,
SPEC W-3), and user-sim does not exist yet, so Slurm job events are not available to it.

## Decision
- Each mock counts the client requests it serves at `GET /sim/stats`. Health checks, `/sim/*` control calls and
  requests carrying `X-Workbench-Probe` (the UI's own polling) are not counted, so drawn traffic is traffic from the app,
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

## Update (issue 118)
The traffic diagram now sits beside the entity list and draws every node as a tile. Injection events carry
the node names they hit (`nodes` in the `/api/flow` event), so a fault diamond travels to that node and flashes a ring on its
tile. Client traffic stays per service: the mocks count requests per service, not per client or per node, so round dots go
from the *Clients* box to a service. The feed still closes when the page is hidden (the dashboard is one screen since issue 129, there are no tabs).

## Update (issue 314): error answers
Each mock now also counts, at the same single site, the answers with a status of 400 or more in `errors` beside `requests` at
`GET /sim/stats` (probes, `/sim/*` and health checks stay uncounted; `requests` keeps its meaning). `webui/flow.py` carries
the rise as `err` on the event (only when the mock reports `errors`: an older mock, Teleport, the real Jira, Slurm and
k8s have no such counter and the event has no `err`, which the page reads as "not measured") and the running total as `errors`
in the feed. The page draws up to five dots per event as before, the first `err` of them as triangles instead of circles (shape,
not only colour), and says it in text: the Timeline line ("3 requests to BMC farm, 2 errors") and the service box ("3 requests,
2 errors"). Latency stays out of scope: the mocks answer in microseconds, so a speed encoding would imply a measurement that is not real.
