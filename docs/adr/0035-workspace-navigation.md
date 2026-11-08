# 0035. Workspace navigation and a Traffic & Communication view

Status: accepted (issue 394)

## Context
The page grew by one top-bar chip or menu item per feature. The owner asked for workspaces (Infrastructure, Traffic & Communication, Scenarios, Requests & Logs) with the topology as the centre, for several clusters later.

## Decision
- A left navigation replaces the tab strip and the Run/Configure menu items; its tabs keep their ids, so deep links, tests and `ui_shots.py` still work. SPEC W-1 holds: one static page, vanilla JS, no new dependency.
- Traffic & Communication is built from data that already exists: the declared graph (`webui/graph.py`) and the request counters (`webui/flow.py`). Evidence is shown as **observed** (a counter backs it) or **configured** (declared wiring only), never invented.
- Fleet and cluster-switcher views are not built until epic 256 provides real clusters (owner's decision): no demo or hardcoded clusters.

## Next (issues 395-398)
Done in issue 396: `webui/comms.py` holds the documented Slurm, Kubernetes, Teleport, BMC, BCM, GPU exporter and Jira links (protocol, port, plane). Done in issue 397: measured probe latency, slurmctld request counts (`sdiag`, through the read-only governor command `slurm-stats`, since the page has no Docker access, W-3), and the monitoring links from Prometheus's own targets API (`webui/monitoring.py`, kept out of the architecture snapshot so no new boxes appear). Done in issue 398: filters by plane, software and evidence, a link inspector, per-second rates and path following. Done: the Kubernetes API server's total request count, read through its own `metrics` user (`/metrics` only, so the monitoring client's `viewer` role is unchanged). Done in issue 400: slurmdbd is observed through one `sacctmgr` query added to the read-only `slurm-stats` command (shown only when accounting is configured); shared storage is planned for later and stays grayed as "planned, not built yet". Done in issue 401: dark-first theme, the fixed-architecture Traffic diagram (parts a cluster lacks are grayed, never dropped) with flowchart routing, drag (a Move button on touch screens), zoom and Fit, an activity timeline kept only in the page, KPI cards and an All / Topology / Nodes view choice in the left rail. Later work (replay, a Metrics tab, per-cluster views) is issue 402.
