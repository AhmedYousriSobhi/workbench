# Web UI redesign: handoff brief

This folder carries the approved UI redesign from a design session into the repository. Read this file first, then
open the mockups for visual reference.

## What is in this folder

| File | Screen | Interactive in the mockup |
|---|---|---|
| `mockups/Main.dc.html` | 1 · Overview: task-first home | no |
| `mockups/TestRun.dc.html` | 2 · Test run: scenario, expectations, Client's eye | no |
| `mockups/Topology.dc.html` | 3 · Topology: flat view, lenses, node inspector | no |
| `mockups/Setup.dc.html` | 4 · Cluster setup: review changes before Apply | no |
| `mockups/Architecture.dc.html` | 5 · Single-cluster live circuit with traffic layers | yes (layer stack) |
| `mockups/Traffic.dc.html` | 6 · Traffic analysis: cluster scope, focused wire, request river, pocket probe, rhythm | yes |
| `mockups/Zoom.dc.html` | 7 · Infrastructure zoom: fleet → cluster → pool, signal opens in Traffic | yes |
| `mockups/canvas.json` | Board titles and order | – |

**The mockups are reference, not code to ship.** They are `.dc.html` design files: their markup uses `{{holes}}`,
`<sc-for>`/`<sc-if>` tags and a `DCLogic` class that only run inside the design canvas, and all numbers in them are
sample data. Read them for layout, colours, wording and behaviour; rebuild the screens in the repository's own stack.
The `renderVals()` method at the bottom of each interactive file is the clearest description of the behaviour (what
changes when a level, layer, filter or mode is picked).

## Constraints the implementation must keep

- ADR 0007: no framework, no build step. Use native ES modules served as static files (one module per workspace),
  plain DOM, SVG for diagrams. Keep the stdlib/Starlette backend.
- W-1: the UI backend stays within its 50 MB budget. No thread pools for fan-out (see the comment in `webui/cluster.py`).
- W-11: every live feed and animation stops while the tab is hidden (`visibilitychange`), including the governor SSE.
- Accessibility: status is never colour alone (icon + text everywhere); every animation is disabled under
  `prefers-reduced-motion: reduce`; `role="status"` regions are written only when their text changes.
- Security: build DOM nodes (or an escape-by-default tagged template) for any text that comes from mocks; plan a CSP
  with script hashes once the page is split into modules.
- The diagram is one fixed architecture: a part a cluster lacks is greyed, never dropped (W-17). Independent clusters
  are never joined by wires.

## Visual language (shared by screens 5, 6, 7)

- Ground `#070B10` with a 24 px dot grid (`radial-gradient(circle, #141D27 1px, transparent 1.4px)`); surfaces
  `#0A1017` / `#0C121A`; borders `#1B2430` / `#22303F`; text `#F1F5F9`, secondary `#C9D3DD`, muted `#8FA1B5`.
- Plane colours: access `#5CB8FF`, control `#E2E8F0`, Kubernetes control `#7C9CFF`, data `#C49BFF`,
  monitoring `#4BE3A0`, management `#FFB347`; error `#FF5D5D`; no current `#3A4656` dashed.
- Fonts: Inter for text, JetBrains Mono for addresses, ports, counts and message names.
- A wire is four stacked SVG strokes: dark base (7 px), plane-colour halo (9 px, 12 % opacity), thin core (1.5 px,
  55 %), and a pulse stroke with `stroke-dasharray: 2 18` animated by `@keyframes flow { to { stroke-dashoffset: -20 } }`.
  Pulse speed follows the request rate; errors add a red `1 60` dash animated on a 61-unit loop. Keep dash periods
  divisible into the keyframe offset or the loop visibly jumps.
- A part that needs attention breathes amber (box-shadow keyframes) and always carries text ("▲ drained · XID 79").

## Screen behaviour

### 7 · Infrastructure zoom (the main view)

- Three levels: Fleet, Cluster, Pool. Each level is a full layer. Changing level is a transient: the outgoing layer
  scales (2.6× when zooming in, 0.35× when zooming out) and fades, then becomes `visibility: hidden` so it never stays
  in the background; the incoming layer grows from the clicked card or rack (`transform-origin` = that element's
  centre). About 0.7 s, `cubic-bezier(0.2, 0.8, 0.2, 1)`.
- A breadcrumb (Fleet › cluster › pool) and a zoom rail with − / + and stops FLEET, CLUSTER, POOL, then TRAFFIC ↗.
- **Fleet:** one card per cluster with the same structure: header + health pill, capability badges (✓ present,
  dashed "no controller" when absent), three KPI tiles (nodes in service, live current, errors), a Slurm lane and a
  Kubernetes lane (one square per node), a live wire footer. Only the monitoring client connects to each cluster.
- **Cluster:** the screen-5 circuit, generalised. Slurm lane (login → slurmctld/controller → CPU and GPU pool racks,
  slurmdbd) and Kubernetes lane (kubectl → kube-apiserver + etcd, KWOK → API, CPU and GPU pool racks). With a
  controller: the slurmctld chip reads "controller · sshd + cmsh · BCM head node" and an admin SSH :2221 wire is
  drawn; without one, login wears a "HEAD NODE · cmsh" badge. Racks show one square per node up to 64, one square per
  8 nodes above that. Services row on an HTTP bus. Traffic-layer stack on the right isolates one plane (others fade
  to 10 %). Absent lanes stay in place at 8 % opacity with a one-line note.
- **Pool:** side panel (fed by, KPIs, stacked state bar, filters All/Busy/Idle/Needs attention); a 4 × 4 tile grid,
  16 per page, each tile with its own heartbeat wire, state pill, 8 GPU utilisation bars (or CPU/MEM/LOAD/NET), and a
  "Wire in Traffic ↗" link.

### 6 · Traffic analysis

- Header cluster switcher (scope). Opening from the zoom shows a **Focused wire** panel: path, a tube where message
  capsules travel both ways (requests →, replies ←), a message log and a decoded view, "Back to the pool" and
  "Clear focus". Pass the cluster and wire in the URL hash (plain `#name`, for example `#cluster-a-gpu003`, per the
  repository's hash rules).
- **Request river:** three columns (who calls → who answers → how it ended: 2xx, slower than p95, 5xx). Band width
  ∝ √(requests/min), particles along each band, errors peel off to the 5xx outlet. Stream chips spotlight one stream.
- **Pocket probe:** five modes (Rate, Delay, Errors, Rhythm, Path), big reading, mini trace, one-line verdict.
- **Rhythm:** detected request periods as beating columns, each labelled with who runs that timer; unknown timers
  flagged.
- **Found in this window:** anomaly list (no retry after 5xx, synchronized bursts, unexplained timers).

### 5 · Architecture (single cluster)

Same as the cluster level of screen 7, for a one-cluster setup. Keep it as the default view when only one cluster
exists.

### 1–4 · Overview, Test run, Topology, Cluster setup

See the mockups; the full rationale is in `architecture-review.md` (Part 11 and the UI section).

## Data the new screens need

Existing routes to reuse: `/api/status`, `/api/events`, `/api/cluster`, `/api/flow`, `/api/slurm-stats`,
`/api/monitoring`, `/api/connections`, `/api/nodes/{node}`, `/api/plan`, `/api/plan/apply`, `/api/scenarios*`,
`/api/nhc`. Missing, in order of need:

1. Per-path request counters in bmc-farm, bcm-mock and jira-mock (method, path, status, latency), plus a log of
   unknown paths. Needed by the river, the probe, rhythm detection and the focused wire.
2. One bulk summary endpoint per mock (all nodes in one call) instead of per-node polling.
3. A multi-cluster inventory: clusters, which parts each has (Slurm, Kubernetes, controller, accounting), pools
   with node counts and states. Derived from the same generated inventory every mock reads.
4. Message capture for one wire (Slurm RPC types from slurmctld's RPC stats; Kubernetes request audit for the API
   server), sampled, only while the focused-wire panel is open.
5. Period detection: autocorrelation over each stream's request timestamps in the window.

## Suggested build order

1. Split `webui/static/index.html` into ES modules without changing behaviour; add `node --test` for pure logic.
2. Shared wire component (SVG strokes, speed from rate, reduced-motion off switch) and the colour tokens above.
3. Screen 7 Cluster level from current data (single cluster), with greyed absent parts and the controller variants.
4. Pool level, then Fleet level and the zoom transition.
5. Per-path counters (backend), then Traffic analysis: river, probe, rhythm, focused wire with hash routing.
6. Screens 1–4.

## Acceptance checks

- Changing zoom level never leaves the previous level clickable or visible after the transition.
- A cluster without Kubernetes, without Slurm, or without a controller renders the same template with the right
  parts greyed and the right badge.
- Every animation stops with the tab hidden and under reduced motion; numbers keep updating only while visible.
- Every status has an icon and text; no colour-only state.
- UI backend RSS stays under 50 MB with 32 nodes and two open tabs.
