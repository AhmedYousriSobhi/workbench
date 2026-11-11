# HPC/AI Cluster Simulator: Architecture Review

Scope: read-only review of the repository (no code executed, nothing modified). Goals: speed up the app, lower memory,
and make the infrastructure use real components where it matters to a monitoring client. File references are
repo-relative.

---

## Part 1. Startup speed

| # | Finding | Where | Recommendation |
|---|---|---|---|
| S1 | Slurm compiles with `make -j2` and no build cache on every cold build | `images/node-base/Dockerfile` | `make -j$(nproc)` with a BuildKit cache mount or ccache; publish the pinned base image to a registry so new machines pull instead of compile (see security note SEC1 first) |
| S2 | Every start stage runs `docker compose up --build` (about 12 stages), and every node service carries the same build context | `benchctl/stack.py` (`up`), `benchctl/compose.py` (`generate`) | Build each image once up front, then drop `--build` from the per-stage calls |
| S3 | Stages are strictly serial although most are independent (observability, mocks, Jira, Kubernetes do not depend on Slurm) | `benchctl/stack.py` `STAGES` | Model startup as a dependency graph; start independent branches together |
| S4 | "Running" is treated as "ready": `wait_running` polls `compose ps` every 2 s; slurmd can start before slurmctld answers and the restart policy hides the race | `benchctl/stack.py` `wait_running` | Real healthchecks (`scontrol ping`, HTTP `/healthz`), `depends_on: condition: service_healthy`, one `compose up --wait` |
| S5 | Debug tools (tcpdump, strace, vim, traceroute, lsof...) in the node base image inflate build, pull and layer size | `images/node-base/Dockerfile` | Move them to an optional debug image |

## Part 2. Runtime speed: UI and governor hot paths

| # | Finding | Where | Recommendation |
|---|---|---|---|
| R1 | Cluster snapshot makes 2 HTTP calls per node on a fresh TCP connection, plus a full GPU-exporter scrape parsed by regex, every 3 s per open page (about 66 calls at 32 nodes) | `webui/cluster.py` `snapshot`, `_node` | One bulk endpoint on bmc-farm (all nodes' power, temperature, fans) and a compact `/sim/summary` on the exporter: O(1) calls, no thread pool needed |
| R2 | One unreachable mock stalls the traffic feed: serial polling with a 2 s timeout while holding a lock | `webui/flow.py` `poll` | Short timeout (about 200 ms) and back-off for mocks seen down recently |
| R3 | Each SSE stream calls the governor socket every second, per tab (two streams per tab) | `webui/app.py` `events`, `flow_events` | One background task fetches status once per second and fans out to all streams |
| R4 | Default (user-mode) governor forks the `docker` CLI for freeze and stop, exactly when memory is scarce; GV-8 cannot hold in the mode most people run | `governor/executors.py` | Talk to the Docker Engine API over the Unix socket on a connection opened at startup; no fork |
| R5 | In user mode, L3 "freeze" (`docker pause`) frees no memory; reclaim needs root | `governor/executors.py` | Recommend rootless Docker (user owns the cgroups: `memory.high`, `memory.reclaim` work), or skip freeze in user mode and stop the lowest class directly |

## Part 3. Memory

| Target | Today | Strategy | Rough saving |
|---|---|---|---|
| Per-node BMC forwarders | Python asyncio, about 18 MB each (`components/bmc-farm/forward.py`) | Static socat or Go binary on `scratch` | About 16 MB x nodes (about 0.5 GB at 32) |
| Prometheus | 200 MB tmpfs inside a 300 MB limit, time-based retention only (`compose/observability.yml`) | `--storage.tsdb.retention.size`, disk volume (reclaimable page cache) | Removes an OOM risk |
| MariaDB | 204 MB measured, mostly its tmpfs (`benchctl/compose.py`) | Disk-backed with `innodb_flush_log_at_trx_commit=0`, or a smaller tmpfs | About 100 MB, and reclaimable |
| slurmd x N | 13 MB each, plus an unmeasured containerd-shim per container | Slurm multiple-slurmd mode (N logical nodes per container) for `full` | Shim overhead x N |
| Python mocks | 4 interpreters at 12-20 MB | Optional single "mock-hub" process; keep separate by default so the governor can freeze them individually | About 30 MB |
| Teleport | 222 MB of a 256 MB cap | Raise the cap or tune before it is OOM-killed under load | Stability |
| Measurement | `docker stats` only (`benchctl/memory.py`) | Add host-side PSS of shims and dockerd to the plan | Honest budgets |

Principle: anything multiplied by N nodes must cost a few MB or be shared; higher-fidelity parts go on K nodes only (the
`--dcgm-nodes` pattern, generalised).

## Part 4. Realism gaps a monitoring client would notice

| # | Finding | Where | Recommendation |
|---|---|---|---|
| M1 | One Prometheus target for the whole farm, so `instance` is the farm, not the node; real DCGM and node_exporter have one target per node | `observability/gen_targets.py` | Per-node scrape addresses (reuse the per-node IP plan) or relabelling |
| M2 | Prometheus scrapes only the farm and the governor: not the real DCGM exporters (`:9402+`), the Kubernetes API server, KWOK, Teleport or Slurm | `observability/prometheus.yml` | Generate scrape jobs from the selected parts |
| M3 | `JobAcctGatherType=jobacct_gather/none`: `sstat` and `sacct` have no CPU or memory usage | `benchctl/slurmconf.py` | `jobacct_gather/linux` reads `/proc`, needs no privilege, gives real job-efficiency data |
| M4 | Filesystem metrics are random per scrape and live inside the GPU exporter (`/mnt/lustre` only) | `components/gpu-exporter-farm/exporter.py` | Real node_exporter textfile collector driven by a `storage:` section in `sim.yaml` |
| M5 | No Alertmanager and no alert rules (SPEC C-7) | `observability/` | Real Alertmanager (about 20 MB), rules for XID, InfiniBand, thermal; webhook into jira-mock |
| M6 | No Slurm metrics in Prometheus | `observability/` | Slurm's own metrics endpoint if the pinned version has one, otherwise a standard Slurm exporter |

## Part 5. Storage strategy

The rules (SPEC C-11, CLAUDE.md) forbid NFS/Lustre servers, and unprivileged containers cannot mount them.

1. Scenario-driven fake storage (about 0 MB). A `storage:` section in `sim.yaml` (mounts `/home`, `/scratch`, `/lustre`, sizes, per-user quotas) generates textfile metrics plus `df`/`lfs quota` shims on the login node (same pattern as the fake `cmsh`). This enables `disk-full`, `quota-exceeded` and `stale-mount` scenarios.
2. A real shared POSIX filesystem with no server. One Docker named volume, backed by a host directory, mounted at `/home` and `/scratch` on login and every slurmd. Job output really appears on the login node; no privilege; only reclaimable page cache.
3. A real storage service. MinIO or SeaweedFS (about 100-150 MB) as an optional P6 part for S3-style checkpoints. Changes SPEC: needs an ADR and the owner's decision.

Recommended: 1 + 2 together.

---

## Part 6. Deeper gaps (second pass)

### 6.1 Inventory consistency: the same node has different shapes in different services

SPEC section 3 says every generated artifact must derive from `sim.yaml` so nodes agree everywhere. They do not:

| Source | GPU node CPUs | GPU node memory |
|---|---|---|
| `benchctl/slurmconf.py` (Slurm) | `compute.cpus` (8) | `compute.memory_mb` (4096 MB) |
| `components/bmc-farm/bmc.py` (Redfish) | `8 x gpus_per_node` (64) | `2048 MB x gpus_per_node` (16 GB) |

A client that correlates Slurm with Redfish sees a node with 8 cores in one place and 64 in another. Real H200
nodes have far more of both.

Root cause: each mock re-parses `sim.yaml` with its own logic (`bmc.py`, `bcm.py`, `exporter.py`,
`gen_targets.py`, `benchctl/nodes.py`), even though `benchctl/inventory.py` exists.

Recommendation: add `gpu.cpus` and `gpu.memory_mb` to `sim.yaml`, have `benchctl` emit one generated `inventory.json`
(names, IPs, shapes, GPU UUIDs, serials), and make every mock read only that file. Add a test that compares the
shapes across Slurm, Redfish, BCM and DCGM.

### 6.2 Governor signal handling

| # | Finding | Where | Recommendation |
|---|---|---|---|
| G1 | Swap-out rate is sampled but never used by the ladder (GV-2 lists swap rate) | `governor/signals.py`, `governor/ladder.py` | Add a swap-storm condition (sustained pswpout) as an L2/L3 trigger |
| G2 | `memory.events` (oom, oom_kill, high) per cgroup is not read (GV-2) | `governor/signals.py` | Read it: a rising `high` count shows throttling working; `oom_kill` is an audit event |
| G3 | Polling at 1 s on `avg10` reacts on a 10-second average | `governor/__main__.py`, `signals.py` | Use the kernel PSI trigger API (write `some 150000 1000000` to `/proc/pressure/memory`, then `poll()`): event-driven, sub-second, cheaper than polling |
| G4 | Signals read with `read_text()` every tick: fresh allocations each time, against GV-8 "no dynamic allocation" | `governor/signals.py` | Pre-opened file descriptors and `os.pread` into preallocated buffers |
| G5 | Thresholds are absolute GB defaults, independent of host size and `sim.yaml` `host:` | `governor/ladder.py` `Thresholds` | Derive defaults from total RAM and `host.reserve_gb`/`safety_floor_gb`; one source of truth |
| G6 | zram/zswap state is not considered (P-3 tuning is still to do) | `governor/` | Include compressed-swap usage when it is enabled, or MemAvailable overstates headroom |

### 6.3 Security coupling with the speed recommendation

| # | Finding | Where | Recommendation |
|---|---|---|---|
| SEC1 | The munge key and the Slurm JWT signing key are generated inside the base image layer | `images/node-base/Dockerfile` | Generate them at `up` into the gitignored secrets directory and mount them. Required before publishing the image (S1): a published image would publish the keys |
| SEC2 | Web UI accepts its token as a URL query parameter | `webui/app.py` `login` | POST form or one-time code; keep the cookie |
| SEC3 | Kubernetes API server runs with `--allow-privileged=true` although nothing privileged is scheduled | `benchctl/k8s.py` | Set to false; closer to a hardened real cluster |
| SEC4 | TLS verification disabled in CLI calls to Teleport and Kubernetes | `benchctl/teleport.py`, `benchctl/k8s.py` | Pin the generated CA instead of disabling checks |

### 6.4 Redfish (BMC) realism

`components/bmc-farm/bmc.py` covers Systems, Chassis Thermal/Power, Managers, SessionService, UpdateService, log
entries. Missing compared with a real HGX-class BMC:

- **GPUs as Redfish resources.** The GPU count is an `Oem` field. Real HGX BMCs expose each GPU as a `Processor` with `ProcessorType: GPU` (with health, temperature, serial). A client inventorying GPUs out-of-band finds none.
- **EventService and subscriptions.** Real BMCs push alerts (Redfish event subscriptions, SSE). Only polling is possible today.
- **TelemetryService / MetricReports.** Used by newer fleet tooling for bulk sensor reads.
- **PCIe devices and NICs** (`PCIeDevices`, `NetworkAdapters`), which tie InfiniBand faults to hardware.

### 6.5 BCM surface

`components/bcm-mock/bcm.py` implements only `POST /json` (cmdevice, cmstatus, cmpart). The architecture document
(section 4.4) names the CMDaemon REST monitoring API (`/rest/v1/monitoring/...`: entities, measurables, usage). It is
not implemented, so monitoring data through BCM is unavailable. Open decision 2 (which surface the client uses) is
still unresolved.

### 6.6 Kubernetes: real, light components an AI cluster would have

The control plane is real and KWOK is efficient. To look like an AI cluster at low cost:

| Component | Why | Approx. RAM |
|---|---|---|
| kube-state-metrics (real) | Real Kubernetes object metrics in Prometheus | About 30 MB |
| Kueue (real) | Standard AI batch queueing on Kubernetes; works against KWOK pods | About 50 MB |
| DCGM series for KWOK GPU nodes | GPU metrics for Kubernetes GPU nodes, like the Slurm GPU nodes | Reuse the exporter farm |

Already noted as not built (ADR 0034): a real agent node, metrics-server, a k-login node.

### 6.7 Scenario engine: scripted effects instead of emergent ones

- `scenarios/gpu-xid-storm.yaml` injects an XID, then drains the node itself 90 s later and ramps the BMC temperature by script. The real system already has the reaction: NHC drains on XID and `node_watch.py` files a ticket. The scenario scripts the effect instead of testing the reaction.
- Recommendation: a fault model. One definition per fault (for example "GPU fell off the bus") applies every physical effect consistently (DCGM missing GPU, `nvidia-smi`, Redfish event log entry, BCM health check) and the scenario injects only the root cause, then asserts the system's reaction.
- Only 15 of 29 scenarios have `expect` assertions.
- Scenarios hard-code node names (`gpu001`), which break when `sim.yaml` prefixes change. Use selectors (`first gpu node`, `any 2 gpu nodes`).

### 6.8 Tests and CI

- Unit tests depend on host tools (`ssh-keygen` in `stack.up`), so they are not hermetic and would fail in a clean CI runner.
- The only workflow is the wiki sync (`.github/workflows/wiki.yml`); nothing runs `validate.sh` or scenarios in CI.
- The component catalog still lists `scenario-engine` as a container (`benchctl/compose.py` `CATALOG`), but ADR 0006 moved it into the CLI. The same applies to the empty `components/user-sim`, `ticket-bot`, `scenario-engine` folders.

---

## Part 7. Gaps compared with the defined architecture and SPEC

- GV-10: no `systemd-oomd` backstop; only the governor slice exists in `systemd/`.
- P-3: host tuning (zram, swappiness, THP) and `benchctl top` are still to do.
- C-12: neither `tc netem` nor a Slurm-path network fault (needs root on the host bridge, which conflicts with the no-sudo rule).
- C-5: jira-mock `cloud`/`dc` modes and the `jira mode` CLI verb (SPEC section 7) are not built.
- C-7: Alertmanager is missing.
- C-8: user-sim is a Poisson `sleep` submitter only; personas, failure behaviour and daily rhythm are missing; no ticket-bot.
- SPEC section 3: `sim.yaml` should hold profile budgets and QOS definitions; budgets live in `profiles/*.yaml` and QOS is a per-user label only.
- W-7: Budget Manager not built.
- NF-2: contract-test tooling exists, but no real Jira shapes are recorded.
- Phase 8: no CI.
- Documentation drift: the architecture diagram shows k3d/kind with a real agent (rejected by ADR 0034); `full` promises 200 KWOK nodes while `sim.yaml` defines 4; `progress.yaml` calls budgets partial while the memory document says they were measured.

---

## Part 8. Target architecture strategy

1. **Three fidelity tiers per component**, chosen in `sim.yaml`: mock, real on K nodes, real everywhere.
2. **One inventory**: a single generated `inventory.json` consumed by every mock, so shapes, IPs and UUIDs agree.
3. **One readiness model**: healthchecks everywhere, a dependency graph, one `--wait`.
4. **Push over poll**: one summary endpoint per mock, one shared poller in the UI.
5. **A governor that never forks and reacts to events**: Docker Engine API on a pre-opened connection, PSI triggers, rootless Docker as the recommended user mode.
6. **A fault model, not scripted effects**: scenarios inject causes and assert reactions.
7. **Honest budgets**: host-side PSS counted; a per-node cost test for every N-multiplied part.

## Part 9. Suggested order

| Order | Item | Effort | Impact |
|---|---|---|---|
| 1 | Drop per-stage `--build`, parallel independent stages (S2, S3) | Low | Startup time |
| 2 | Single inventory file and fix GPU node shapes (6.1) | Low-medium | Correctness across all services |
| 3 | Replace per-node forwarders (memory table) | Low | About 0.5 GB at 32 nodes |
| 4 | Per-node Prometheus targets, broader scrape jobs, `jobacct_gather/linux` (M1-M3) | Low | Realism |
| 5 | Bulk BMC endpoint and shared UI poller (R1-R3) | Medium | UI responsiveness |
| 6 | Keys out of the image (SEC1), then publish the base image (S1) | Medium | Cold start in seconds |
| 7 | Governor: Docker API, PSI triggers, swap and `memory.events` (R4, G1-G4) | Medium | Safety under pressure |
| 8 | Storage tiers 1 + 2, Alertmanager (Part 5, M5) | Medium | Realism |
| 9 | Fault model in the scenario engine (6.7), hermetic tests and CI (6.8) | Medium-high | Test value |

---

## Part 10. Decisions (confirmed)

| Issue | Decision | Follow-up |
|---|---|---|
| #453 | Keep the user-mode ladder as ADR 0010 defines it. Reject rootless Docker: its bridge network lives in a separate namespace, so the host cannot reach the fixed 10.141.x addresses (NF-8). | ADR 0010 addendum. Narrow the issue to (1) "skipped in rootless mode" entries in the audit log and the page (GV-6), (2) #476. Rename the issue title. |
| #457 | Mock-hub: won't do (one container has one IP, which breaks the fixed service addresses). Multiple-slurmd: never the default and not built now; if it is ever built, only as an explicit opt-in scale mode in its own issue. | ADR. Update architecture sections 4.1 and 13 (multiple-slurmd is no longer the 32+ node mitigation). Revisit only if a client test needs more than about 64 Slurm nodes or #456 shows per-container overhead breaking a budget. |
| #478 | SPEC C-12 becomes: "userspace fault proxies on client-facing paths (default); netem inside the cluster only through the root service, with bounded targets, a TTL, and L0-L1 only." | ADR, SPEC C-12, architecture 4.5 and 4.9 (both still name Toxiproxy and "tc netem, no proxy"). The user-mode governor refuses netem with one message naming the install script. netem is CLI and scenario only, not offered on the page. |
| #455 | MariaDB moves to disk-backed storage (`innodb_flush_log_at_trx_commit=2`) only if the `shmem` measurement shows a meaningful saving (proposed threshold: at least 50 MB). Prometheus retention by size and the Teleport cap go ahead now. | Measure `shmem` in the mariadb container's cgroup `memory.stat`, idle and after a loaded run on the 2026-11-05 selection. If it passes: SPEC C-11, ADR, `remove accounting-state` verb. Fix the "about 200 MB each" vs `size=100m` mismatch either way. |

---

## Part 11. UI/UX review (read-only)

Shape of the page: one `webui/static/index.html`, 2,389 lines, 285 KB (about 176 KB JavaScript, 70 KB CSS, 39 KB
markup), 136 named functions in global scope, 55 `innerHTML` writes, 5 dialogs (Scenarios, Governor, Export, Cluster
architecture, Health checks). Backend `webui/app.py` with about 28 routes.

### 11.1 Defects (spec or behaviour)

| # | Finding | Where | Fix |
|---|---|---|---|
| U1 | The governor SSE (`/api/events`) opens at load and is never closed when the tab is hidden; W-11 says live feeds run only while visible. Each hidden tab keeps one governor status call per second. | `index.html` (`new EventSource('/api/events')`); `visibilitychange` closes only the flow feed | Close and reopen it on `visibilitychange`, like the flow feed |
| U2 | Cluster polling continues while hidden (every 15 s instead of 4 s) | `loadCluster` | Stop while hidden (W-11) |
| U3 | `role="status"` regions (`#banner`, `#conn`) are rewritten every second with the same content; screen readers can announce on every rewrite | `render()` | Write only when the text changes |
| U4 | The Connections cache is never invalidated: after applying a plan that adds Teleport, a jump host or login nodes, Connections and node panels stay stale until reload | `connCache` | Reset it when an apply finishes or the part set changes |
| U5 | Apply-plan progress is inferred by matching audit-log text, with a 10-minute timeout; log wording becomes an API | plan apply handler | The governor returns a job id and exposes stage progress (the `say` lines of `stack.up`) in `status` |
| U6 | Applying a plan can remove nodes (and their running jobs) and restart slurmctld with no summary or confirmation; only "remove check" asks | Cluster architecture sheet | Show the diff before Apply: "removes cn003, cn004; restarts slurmctld; stops N running jobs" |
| U7 | A scenario played from the page skips steps that need Docker or the host; the page reports them as skipped only after the run | Scenarios sheet, `app.py` (`host=False`) | Mark page-unavailable steps in the timeline before Play, with the CLI command for a full run |
| U8 | `fetch('/api/nodes/${name}')` interpolates the node name without `encodeURIComponent` | `index.html` | Encode path segments |

### 11.2 Security hardening

- Every write path uses `innerHTML` with an `esc()` helper, and values are pre-escaped when they are built (for example the node-connection key/value list). It works today but is fragile: one missed `esc` is an injection, and some rendered text comes from mocks other people can write to (Jira summaries and comments). Build DOM nodes for anything that carries mock data, or add a tagged-template helper that escapes by default.
- There is no Content-Security-Policy. The page is one file with inline script and style, so a CSP needs hashes. It is worth adding because the page holds a session cookie that can drive node faults and plan applies.
- The login token arrives in the URL query (`/login?token=`), so it lands in browser history (already listed as SEC2).

### 11.3 Speed and maintainability

- All 176 KB of JavaScript parses and initialises at load, including views the user may never open (2.5D, clusters, health-check editor, plan sheet). Native ES modules served as separate static files keep ADR 0007's "no framework, no build step", allow per-view lazy loading, and make the logic unit-testable with `node --test`. Today only Playwright screenshot checks exercise the page; logic such as BMC group naming and plan totals has no tests.
- The JavaScript is in one global scope with 136 functions; module boundaries (governor, cluster, traffic, plan, scenarios, nhc, help) would follow the existing dialogs one to one.
- The backend fans out per request (see R1-R3 in Part 2); the page itself adds U1 and U2.

### 11.4 UX and information architecture

The page serves one persona: a developer testing a monitoring client. Their jobs are (1) is everything up, (2) break
something, (3) what did my client call and see, (4) how do I connect. The page has strong pieces for each (Needs
attention, scenarios, Client's eye, Connections), but they live in different places:

- **Missing core flow: a test session.** Scenario (a sheet), its expectations (inside the sheet), Client's eye (in the architecture view) and the traffic timeline (another workspace) are never shown together. A "Test run" view would combine: pick a scenario, see which client-visible effects to expect, play it, watch Client's eye and expectations pass or fail live, then export a report (what was injected, what the client called, what passed). The report is what a client bug report needs; today it must be assembled by hand.
- **Feature weight vs. the core jobs.** 2.5D, Flat and Clusters views, lenses, focus, blast radius, drag-reorder of parts and groups, a touch Move mode and a layout reset all sit on the main view. They are well built, but most serve presentation rather than the four jobs. Consider Flat as the default on every device (it is already the default under reduced motion) and 2.5D as an opt-in.
- **Phone layout has little reach.** The page binds to localhost (W-8), so a phone cannot open it without port forwarding. Narrow-window support matters (a side-by-side IDE); touch-specific work (the Move button, single-tap entry) is low value. Keep W-9's narrow-width rules, drop new touch features from scope.
- **Small, fixed type.** Most text is 12-14 px in fixed `px` units, and SVG labels are about 11 px scaled. Zoom still works, but the browser's own font-size setting is ignored. Move to `rem`, with 14 px as the floor for body text.
- **Spec readability.** SPEC W-17 is one requirement of several hundred words covering a dozen behaviours. Split it into atomic requirements with their own IDs so each can be tested and ticked off.

### 11.5 Suggested order

1. U1-U4 and U8 (small, spec compliance and correctness).
2. U6 plan diff and U7 pre-play marking (safety and honesty, both small).
3. Test-run view with an exportable report (largest UX value).
4. Split the page into ES modules, add `node --test` for the logic, then a CSP.
5. Flat as the default view, `rem` type, W-17 split.
