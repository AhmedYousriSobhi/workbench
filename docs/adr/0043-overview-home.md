# ADR 0043: Overview is the home workspace (redesign step 6a)

Status: accepted. Part of the UI redesign (epic 491); builds on ADR 0007, 0038 to 0041.

## Context

The page served one persona with four jobs: is everything up, break something, what did my client call, how do I connect. Its first
view was the Infrastructure diagram, which answers none of them at a glance. The approved design (`docs/design/ui-redesign/mockups/Main.dc.html`)
puts a task-first Overview in front.

## Decision

- A new workspace, **Overview** (`tab-home`), is the default for a new session; a saved tab is still restored. The old Infrastructure
  workspace is named **Topology**; its ids (`tab-nodes`, `tb-nodes`) are unchanged so nothing that links to it breaks.
- Overview holds: Needs attention, three start cards, one card per node and the services. It shows data the page already has
  (`/api/cluster`, the governor status); it adds no route and no poll, and it paints only while it is the visible workspace (W-11).
- The attention list has one source (`attentionItems`, pure, in `homemodel.js`) shared by the top strip's bar and Overview, so they cannot disagree.
- Honesty: a node's load bar exists only when the GPU exporter measured it; otherwise the card says what it has ("no live reading").
  State is an icon plus words, never colour alone. Services say real or simulated.
- Cards open existing destinations (Scenarios, Topology, Export) until the Test run workspace (6b) exists.

## Consequences

Browser harnesses that expect the diagram at load set the saved tab to `nodes` in their init script. Screens 2 to 4 (Test run, Topology
lenses and inspector, Cluster setup with a review of changes) follow as steps 6b to 6d.
