# 0024. The Infrastructure architecture is the main view

Status: accepted (issue 192)

## Context
The diagram showed clients, services and nodes, but not how a user gets in: the login nodes (ADR 0023) and the Teleport proxy
(ADR 0020) were missing or only a list row, and the list below or beside it competed for the screen. The point of the page is to
reach every part from one picture.

## Decision
- **Draw the access layer.** One box per login node (its name and how it authenticates: Direct SSH, Teleport only, or both) and the
  Teleport proxy when built. Every line is labelled with its method: `direct SSH` (client to a login node), `Teleport` (proxy to a
  login node, and proxy to the nodes), `jump host` (login node to the nodes, by `ssh` from the login node), and the API lines
  (`HTTP REST`, `HTTPS Redfish`, `tsh · HTTPS`, ...). The login nodes come from the saved workbench through `/api/cluster`
  (`logins: [{name, mode, port}]`); the page still reads files only (SPEC W-3).
- **Wide, not tall.** Three columns on top (clients and faults, access, nodes), the services in a band under them. The service lines
  run down a channel between the two, ordered so that none crosses another.
- **Part and tip.** Hovering any part shows one line (state, how it is reached); clicking it opens a pop-up next to it with the
  address, credential path, a command to copy and buttons (a node's actions, the Connections row). The pop-up is a non-modal
  dialog: Esc closes it and returns the focus. Tips also show on keyboard focus.
- **The node list folds away** (a button, remembered in local storage). Open by default, beside the diagram; folded, the diagram has
  the whole width. Opening a node's panel unfolds it.
- **The options sheet degrades.** If the backend predates `logins` in `/api/settings`, the parts are still listed and the login rows
  are hidden; a real failure names the cure (`benchctl stop`, `benchctl start`).

## Consequences
- The diagram is wider (viewBox 1008 wide) and its height grows with the number of login nodes; the one-screen rule (no page
  scrolling at 1280x900 with six nodes) is checked by `ui_shots.py`, together with the tip, the pop-up and the fold.
- A phone scrolls the diagram sideways inside its panel instead of shrinking it past reading.
- The login nodes' state is not probed (there is no counter for ssh traffic): their boxes show the way in, not a health status, so no dots run on the `direct SSH` lines.
