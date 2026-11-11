# ADR 0039: The Cluster level of the infrastructure zoom (the "Circuit" view)

Status: accepted (owner decisions of 2026-11-11 below). Issue 496, epic 491, builds on ADR 0038.

## Context
Redesign step 3 draws one cluster as the mockups' circuit (`Architecture.dc.html`, `Zoom.dc.html` level 2): a Slurm lane and a Kubernetes
lane, pool racks with one square per node, a services row on an HTTP bus, wires between them and a stack of traffic layers. The page already has a
diagram of the same cluster (2.5D or flat, ADR 0030) with focus, blast radius, a lens, drag-reorder and touch Move, and a declared graph and a
documented-link model (`webui/graph.py`, `webui/comms.py`) behind them.

## Decision
- **A new view beside the old one.** The view switch gets **Circuit** next to 2.5D, Flat and Clusters. The old diagram stays until step 6 rebuilds it as
  Topology (screen 3, where 2.5D and the lenses live, owner decision). Nothing is retired in this step. Focus, blast radius and the lens are kept
  and work on the circuit: parts carry `data-ent` and wires `data-edge`, the same attributes the old drawing uses (`focus.js`, `lens.js` and `blast.js`
  now select `g.wire[data-edge]` as well as `path.lane[data-edge]`).
- **No new backend.** Wires come from the documented links the server already sends (`comms`, plus the monitoring links and the accounting probe).
  A wire names its link by the two ends; a Python test fails if a wire names a link `comms` no longer has.
- **Honest wires (graph.py's rule).** A wire pulses only when a request counter backs it; wiring nothing counts is drawn lit but still
  ("declared", `wire.js` gained a `declared` flag) and its text says "declared wiring, not measured". The two KWOK-to-pool wires are in the mockups
  and in no documented link, so they are declared by design. A part the page cannot probe says "not probed", never "up". The bus carries the sum of
  the service rates.
- **One fixed template (W-17).** `circuitlayout.js` gives every cluster the same parts at the same coordinates. A part or lane the cluster lacks keeps
  its place at 8 % opacity (with a note on a missing lane, "Not in this cluster: no Slurm"). Controller variants: with a controller the controller chip
  reads "controller · slurmctld, sshd + cmsh · BCM head node" and the admin SSH wire is drawn; without one, login wears a "HEAD NODE · cmsh" badge.
  The layout is pure data, tested with `node --test` (`circuitlayout.test.mjs`).
- **Racks:** one square per node up to 64, one per 8 above, coloured by the worst node; a square that needs attention also carries a glyph, and every rack
  says its state in words ("✓ all 4 in service", "▲ 1 out of service: gpu001 drained").
- **Layer stack:** the five planes of `comms` (access, control, data, monitoring, management; the Kubernetes wires are drawn in their own colour and
  belong to Control). Picking one fades every other wire, and the parts only those wires touch, to 10 %. It sits under the drawing as a row of buttons.
- **The circuit takes the width.** On a wide screen the node list folds away while Circuit is on (and comes back when it is off); the drawing is at
  least 1,100 px wide and scrolls sideways below that.

## Consequences
Cluster level of the zoom exists for one cluster. Steps 4 and 5 add the Pool and Fleet levels, the transition, and per-path counters (then wires can show
per-path rates; today a service's wire shows the service's rate). Squares are clickable (the node pop-up) but not individually focusable; the node list is
the keyboard route, as before. Prometheus and Grafana are drawn from the monitoring links, so they show as absent until Prometheus answers.

## Revisit when
The per-path counters land (step 5), the Topology screen replaces the old diagram (step 6), or the layout needs a second, narrower variant for phones.
