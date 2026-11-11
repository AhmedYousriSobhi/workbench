# ADR 0045: Topology lens chips and the node summary (redesign step 6c)

Status: accepted. Part of the UI redesign (epic 491); builds on ADR 0043 and 0044.

## Context

The approved Topology screen (`docs/design/ui-redesign/mockups/Topology.dc.html`) shows the lens as a row of pressed-state chips and a node
inspector that opens on a summary (state, reason, hardware, address, BMC, how to reach it) before the break and fix buttons. The page had the
lens as a select and the node panel opened on actions.

## Decision

- The lens is a group of buttons with `aria-pressed` (and a visible check, never colour alone): All, Access, Management, Monitoring, Tickets,
  Faults, Client's eye. "Monitoring" is the label for the `telemetry` plane; the id, the graph and the API are unchanged.
- The node panel starts with a **Summary** card built by `nodeSummary` (`nodemodel.js`, pure, tested) from the cluster snapshot row and
  `/api/nodes/{name}`; it adds no route and no poll. A value the page cannot read says "not measured" or names the command that can; it is never
  guessed. The page has no Docker, so the Slurm reason is not read here.
- The reach command is the Connections row's ssh command with a Copy button.

## Not in this step

A **Slurm** lens (no graph plane carries the Slurm links; they are `access` today) and a per-node Jira **ticket** row (needs a Jira query per node)
need an owner decision on scope; both are left out and listed in issue 511.
