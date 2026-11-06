# ADR 0029: Levels of detail: a global overview and the full diagram

Status: accepted (issue 321; builds on ADR 0028 and the declared graph, issue 312)

## Context
At 50 or more nodes, or with several clusters, the full diagram stops being legible: tiles shrink to nothing. Network-trace
tools solve this with semantic zoom: an overview first, detail on demand.

## Decision
- **Levels.** `overview` condenses a cluster to one card: clients, access (login nodes and the Teleport proxy) and the node
  groups with a health bar (one segment per node: filled is healthy, an outlined taller segment is not, and the text says the
  count), the services as pills (a mark and the word, never colour alone) and the fault count. `full` is the diagram of ADR 0028.
  Counts come from the snapshot the page already has; nothing is invented.
- **Automatic.** Above 24 nodes the page opens at the overview. The **Overview / Full diagram** button pins a level (remembered
  for the browser session).
- **Moving between them.** Hovering or focusing the card shows a preview of the design underneath (a small HTML diagram, no
  navigation). Double-click, Enter, or the **Open full design** button enters the cluster; a breadcrumb (*All clusters > workbench*)
  and Esc leave it. On a phone one tap enters. A table of clusters (state and counts) is in the page for screen readers.
- **Renderer.** `buildFlow` calls `buildOverview` for the overview level; the overview draws in pixel units of its panel, so text is
  never scaled, and wraps lists by measured width instead of cutting them.

## Consequences
- Only one cluster exists today, so the overview shows one card; the card is a function of a cluster, so several clusters become a
  grid of cards without a new renderer (multi-cluster plan, PR 239).
- The preview is a structured summary, not a scaled copy of the full diagram: cheaper, and readable at any size.
- A group level inside one very large cluster (expand a node group) is still to do.
