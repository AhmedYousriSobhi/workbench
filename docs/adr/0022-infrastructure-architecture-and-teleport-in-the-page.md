# 0022. The page's diagram is the Infrastructure architecture, and it shows Teleport

Status: accepted (issue 187)

## Context
The page drew a "Traffic" diagram of the clients, the mock services and the nodes. Two things changed: Teleport (ADR 0020)
is a service users reach through the cluster and it was not drawn, and the tool is meant to grow to several clusters, so a
panel named after one kind of movement in one cluster no longer fits.

## Decision
- **Rename** the panel to **Infrastructure architecture** (headings, aria labels, docs, SPEC W-4/W-12, `ui_shots.py`). The
  **Cluster architecture** sheet keeps its name: it changes what runs, the diagram shows what runs.
- **Teleport is a service box and a list card, only when the part is built.** The page decides from the saved workbench
  (`compose/generated/workbench.yaml`), so a cluster without Teleport never shows a box that cannot answer (SPEC W-10: hide
  data that cannot exist). Status probes Teleport's diagnostics `/healthz`.
- **Dots come from a real counter.** Teleport has no request counter for clients, but the proxy counts every connection it
  makes to a node (`teleport_connect_to_node_attempts_total`; the name was checked in the 18.11.8 binary and in
  Teleport's metrics documentation). A rise is one event from the clients, exactly like the mocks' counters. If the counter is
  absent the box shows up with no dots; nothing is invented. The diagnostics port 3434 is published on 127.0.0.1 only.
- **Control from the page** is the existing Cluster architecture sheet (Access, Teleport, Apply). The page still has no Docker
  access (SPEC W-3), so creating an invite link (`tctl`) and the `tsh` commands stay in a terminal; the Connections row for
  Teleport shows them copy-ready.

## Consequences
- A Teleport container made before this change has no diagnostics port: one `benchctl up --with teleport` recreates it. Until then the card says `not answering`.
- The diagram has one more service lane, so its height follows the number of services; the one-screen rule (no page scrolling at 1280x900 with six nodes) is checked by `ui_shots.py` with Teleport on.
- A future multi-cluster diagram extends the same panel: this ADR only fixes the name and the service model.
