# ADR 0040: The Pool and Fleet levels and the zoom between levels

Status: accepted. Issue 498, epic 491, builds on ADR 0039.

## Context
Redesign step 4 adds the two levels around the Cluster level (`Zoom.dc.html`): a Pool of nodes below it and a Fleet of clusters above it, and the
move between the three. The page already scopes everything to one cluster (`S.activeCluster`, `scoped()`) and has an overview level that
condenses clusters to cards. The running stack is one cluster; the snapshot carries a `clusters` list only when several exist.

## Decision
- **No new endpoint yet.** Fleet reads the snapshot's `clusters` list, or makes one card from the single cluster. The inventory endpoint of the
  handoff (item 3 of "Data the new screens need") is not needed until several clusters really run; the card shape is fixed in `fleetlayout.js`, so
  that endpoint only has to fill it.
- **Levels map onto what exists.** Fleet is the overview level (`curLevel() === 'overview'`), Cluster is the Circuit view, Pool is `S.pool` (`sc`,
  `sg`, `kc`, `kg`, the four racks). The levels are three layers of the Infrastructure panel (`#fleetview`, the existing diagram scroller, `#poolview`);
  the old diagram and old overview are untouched outside the Circuit view.
- **A transition never leaves the old level behind** (acceptance check of the handoff). `zoom.js` animates the outgoing layer (scale 2.6 in, 0.35
  out, fading) and the incoming one grows from the clicked card or rack, 0.7 s on the handoff's easing; then the outgoing layer is `hidden` and
  `inert`, removed from layout. A move started while another runs ends the first at once. Reduced motion swaps the layers with no animation. A timer
  guarantees the end state when a hidden tab never runs the animation. Escape zooms out of a pool; − / + and the FLEET, CLUSTER, POOL stops move one
  level; + at the Pool opens Traffic.
- **Honest numbers.** The page knows the GPUs busy on a node and the average GPU use; it knows nothing of a node's CPU, memory, load or network. A pool
  where nothing reports use says "not measured" and its Busy and Idle filters are disabled; the average is `n/a`, never 0. The request rate and the
  accounting probe belong to the one running cluster, so a card for any other cluster says "n/a" and "accounting not probed". Tile bars light the busy
  GPUs (the page has no per-GPU percentage). The tile's heartbeat wire is **declared** (lit, not moving) until per-path counters exist (step 5).
- **"Wire in Traffic" opens the Traffic tab.** Pointing it at one wire needs the hash routing of step 5.
- **Pure logic is tested without a browser** (`poollayout.test.mjs`: paging at 6, 40 and 200 nodes, filters, KPIs, state bar, GPU bars, card health and
  structure); `ui_shots.py` checks in a browser that exactly one layer shows after every move.
- **Owner feedback, same step:** the canvas palette read too dark and heavy. The ground and surfaces are lifted to a lighter slate (`#111A26`,
  `#16202E`, `#1A2636`; muted text `#A3B5C9`); the plane colours are unchanged and every contrast test still passes.

## Addendum: one way through the views (owner feedback, same step)
- **The page keeps one width.** `.cols` was a grid item with `margin: 0 auto`, so it shrank to its content and the page was a different width in every
  view (phone-like in one, wide in the next). It now has `width: 100%`. With the Circuit on, the old node list never takes a grid column (the Pool level is
  the list).
- **Circuit is the default view.** The view switch is Circuit and 2.5D (classic); Flat and Clusters are removed (the circuit shows the same parts and the
  zoom's FLEET stop replaces Clusters). The All / Topology / Nodes switch of the left bar is hidden while the Circuit is on. 2.5D and the lenses return in
  screen 3 (Topology), as decided.
- **Traffic is a level.** The zoom rail moved above the tab panels and gained **TRAFFIC** as its last stop; the Traffic tab shows a **Back to the
  cluster** button. Going to or from Traffic zooms the leaving tab away over half the time and grows the arriving one in. The page content of Traffic is
  unchanged until step 5 rebuilds it per cluster.
- **Zoom by gesture:** Ctrl + scroll or a trackpad pinch (one level per gesture, toward the card or rack under the pointer), `+` / `-` and Escape. A plain
  scroll still scrolls the page, which must stay possible (the Connections panel and long pools are below).

## Consequences
- Fleet for several clusters shows structure and health per cluster but no live rate for any but the running one, until the inventory and per-cluster
  counters exist.
- Racks gained an **N nodes ›** button (the way in); the old "N nodes" label is inside it.
