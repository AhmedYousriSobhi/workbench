# Control, Web UI and scenarios

[Architecture index](README.md) · [Project overview](../../README.md)

## 7. One-click UX and control

```bash
benchctl doctor                       # host checks + tuning suggestions
benchctl start                        # one step: machine check, governor, cluster, web page
benchctl up --profile std             # or: pre-flight, then bring a preset up
benchctl up --cpu-nodes 2 --gpu-nodes 4 --with jira   # or build a selection
benchctl status                       # governor level and signals, cluster, web page
benchctl get nodes                    # nodes, services, profiles, scenarios
benchctl scale 24                     # change the compute-node count (governor-approved)
benchctl scenario list
benchctl scenario run gpu-xid-storm   # trigger an issue
benchctl set node cn001 off           # power a node off like the page's button
benchctl load on                      # simulated users submit jobs
benchctl stop                         # or: benchctl down (cluster only)
```

Not built yet: `benchctl top` (a live governor table; use `benchctl status`), `jira mode` (cloud/dc) and `workload run`.

The **Web UI** (section 7.1) mirrors the CLI. `benchctl start` is the one-command launcher.

### 7.1 Web UI

Budget: **< 50 MB** host RAM. No heavy JS frameworks.

| Layer | Choice |
|---|---|
| Backend | Starlette + uvicorn (ADR 0008: FastAPI alone costs 39 MB of the 50 MB) |
| Frontend | One vanilla-JS page, no build step (ADR 0007) |
| Live data | Server-Sent Events, 1 s updates |

**Privilege separation.**

| Component | Privilege | Communication | Responsibility |
|---|---|---|---|
| Governor daemon | `root` | Listens on Unix socket `/run/workbench/gov.sock` | Executes scale/freeze/stop actions, monitors PSI |
| Web UI backend | `workbench` (restricted) | JSON over the Unix socket | Serves HTML, auth, translates clicks into socket commands |

A compromise of the Web UI can only issue the pre-approved commands the socket accepts; it never gets root.

**Modules.**
1. **Cluster view (built):** the Infrastructure architecture (clients, fault injector, the mock services, Teleport when on, and every node as a state tile, with moving dots, #37, #118) beside an entity list (one card per node and service with status and quick facts). A node opens a panel in place of the list with *Actions* (power off, overheat, fan failure, GPU error, then recover) and *Info* (how to connect). Slurm drain and Kubernetes faults are not offered here (no Docker access; use the scenario CLI).
2. **Governor panel (built):** opens from the level and memory chips in the top strip: SSE sparklines for `MemAvailable` and PSI, a color-coded level banner (L0 green, L2 yellow, L4 red) and the audit log of governor actions. A level of L2 or higher, or an offline governor, also appears in *Needs attention*.
3. **Scenario Orchestrator (built, Play/Stop/speed):** a sheet opened from the top strip's Menu: browse and filter the scenarios in `scenarios/`, Play, Stop and Fast-Forward, with a timeline of the run's steps.
4. **Profile & Budget Manager (not built yet):** form editor for `sim.yaml`; sliders for node/GPU counts feed a projected PSS memory calculator, and deployment is blocked if the budget exceeds host safety limits.

### Scenario catalog (YAML, composable)

```yaml
# scenarios/gpu-xid-storm.yaml
name: gpu-xid-storm
description: GPU errors on 3 nodes, thermal rise, Slurm drains, app should open tickets
steps:
  - at: 0s    ; target: gpu-exporter  ; do: {nodes: [gpu003,gpu004,gpu007], xid: 79, rate: 2/min}
  - at: 30s   ; target: bmc-farm      ; do: {nodes: [gpu003], sensor: GPU_TEMP, ramp_to: 92C, over: 2m}
  - at: 90s   ; target: slurm         ; do: {drain: [gpu003], reason: "xid 79"}
  - at: 2m    ; target: ticket-bot    ; do: {sla_profile: p1-fast}
expect:   # optional assertions for automated demo/CI runs
  - jira.issue_created: {labels: [gpu, xid], within: 3m}
```

Starter catalog: `node-down`, `gpu-xid-storm`, `ecc-uncorrectable`, `thermal-throttle`, `fan-failure`, `psu-redundancy-lost`, `slurm-drain-storm`, `job-storm`, `oom-jobs`, `queue-backlog`, `k8s-node-notready`, `pod-crashloop`, `network-partition` (`tc netem`), `disk-full` (fake metrics), `jira-outage` / `jira-429` (via Toxiproxy), `jira-transition-refused`, `ticket-storm`, `bcm-api-timeout`.
