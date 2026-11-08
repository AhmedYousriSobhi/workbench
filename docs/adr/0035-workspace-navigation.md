# 0035. Workspace navigation and a Traffic & Communication view

Status: accepted (issue 394)

## Context
The page grew by one top-bar chip or menu item per feature. The owner asked for workspaces (Infrastructure, Traffic & Communication, Scenarios, Requests & Logs) with the topology as the centre, for several clusters later.

## Decision
- A left navigation replaces the tab strip and the Run/Configure menu items; its tabs keep their ids, so deep links, tests and `ui_shots.py` still work. SPEC W-1 holds: one static page, vanilla JS, no new dependency.
- Traffic & Communication is built from data that already exists: the declared graph (`webui/graph.py`) and the request counters (`webui/flow.py`). Evidence is shown as **observed** (a counter backs it) or **configured** (declared wiring only), never invented.
- Fleet and cluster-switcher views are not built until epic 256 provides real clusters (owner's decision): no demo or hardcoded clusters.

## Next (issues 395-398)
Done in issue 396: `webui/comms.py` holds the documented Slurm, Kubernetes, Teleport, BMC, BCM, GPU exporter and Jira links (protocol, port, plane). Prometheus, Grafana, slurmdbd and storage links are added when the snapshot reports those parts (it does not today). Still to do: real measurements (slurmctld RPC stats, Prometheus scrape stats, API server counts, latency) through a read-only governor command, since the page has no Docker access (W-3); plane filters, a link inspector and following a path.
