# Opti-Sim: One-Click Demo Cluster for the HPC AI Infrastructure App

> A memory-governed, scenario-driven simulation of an HPC/AI cluster that runs on a **16 GB Ubuntu workstation**.
> Purpose: exercise every capability of the app (Slurm, Kubernetes, BMC, BCM, Jira ticketing, workload management, Grafana monitoring) with realistic users, jobs, faults and tickets, and control it all from one CLI.

**Status:** architecture proposal, ready to implement. Memory figures are **planning estimates**; Phase 0 below measures the real ones before anything is locked in.

---

## 1. Problem and diagnosis

| Today | Why it hurts on 16 GB |
|---|---|
| Vagrant VM for compute + Grafana node | Every VM carries a full OS, kernel and page cache (roughly 0.5-1 GB idle each). |
| Separate Vagrant file for K8s nodes | Several full-OS nodes, each running kubelet + runtime. The biggest single cost. |
| Jira in a container | Atlassian recommends about 2 GiB for the app server alone; real Jira is a heavy JVM app. |
| No coordination | Three independent stacks can't see each other, so nothing decides who gets stopped when RAM runs out. |
| Need macOS testing | A macOS VM wants 4-8 GB and cannot legitimately run on this hardware (see section 8). |

**Core insight:** most of what the app talks to is an **API surface** (Slurm, Redfish, BCM, Jira, Prometheus), not a real OS. Simulate the API, keep real software only where fidelity matters, and put a governor outside everything that can freeze or stop things before the host starts swapping.

## 2. Design principles

1. **Containers by default, VMs by exception.** Containers share one kernel and one base image (shared page cache). Keep exactly one real VM for kernel/systemd/hardware-level fidelity.
2. **Simulate APIs, not machines.** One process can emulate dozens of BMCs or GPU exporters; don't run one container per BMC.
3. **Declarative topology.** One `sim.yaml` generates compose files, `slurm.conf`, Prometheus targets and BMC inventories. Scaling nodes = changing a number.
4. **Profiles with a memory contract.** Each profile declares its budget and is refused at `up` time if the host can't afford it.
5. **Observe and control from outside the failure domain.** The governor runs on the host, in its own protected cgroup, and never depends on anything it might stop.
6. **Degrade in a defined order.** Priority classes plus an escalation ladder (throttle, freeze, reclaim, stop), with hysteresis so it doesn't flap.
7. **Everything injectable.** Faults and workloads are named scenarios, triggered by CLI, Web UI button or schedule.
8. **No real disk or network hardware.** Storage is faked with metrics and tmpfs; network faults use kernel `tc netem`, not extra proxies.
9. **Time is a parameter.** A `time_dilation` multiplier compresses hours of cluster activity into minutes.

## 3. Architecture overview

```mermaid
flowchart TB
  subgraph HOST["Ubuntu host, 16 GB (control plane lives here)"]
    GOV["hpcsim-governor<br/>systemd service, protected slice<br/>PSI + cgroup + MemAvailable"]
    CTL["simctl CLI + Web UI (hpcsim user)"]
    SD["systemd-oomd (backstop)"]
  end

  subgraph CORE["P1 core.slice"]
    APP["App under test"]
    PROM["Prometheus + Alertmanager"]
    GRAF["Grafana"]
    SCTLD["slurmctld + slurmrestd (+ slurmdbd/MariaDB)"]
  end

  subgraph MOCK["P2 mocks.slice"]
    BMC["bmc-farm: N virtual Redfish BMCs, 1 process"]
    BCMM["bcm-mock: CMDaemon REST + monitoring"]
    JIRA["jira-mock (default) or Jira Cloud free site"]
    GPUX["gpu-exporter-farm: fake DCGM metrics"]
  end

  subgraph COMP["P4 compute.slice"]
    SLURMD["slurmd containers x N (one shared base image)"]
  end

  subgraph K8S["P3 k8s"]
    K3D["k3d/kind: 1 server + 1 real agent"]
    KWOK["KWOK: fake nodes incl. fake GPUs"]
  end

  subgraph LOAD["P5 load.slice"]
    USERS["user-sim: personas and job arrivals"]
    TBOT["ticket-bot: fake engineers working Jira"]
    CHAOS["scenario-engine: fault injection"]
  end

  subgraph VM["P6 on-demand fidelity VM (libvirt/KVM)"]
    FVM["Real Ubuntu node: systemd, sshd, node_exporter, real kernel"]
  end

  CTL --> GOV
  CTL --> CHAOS
  GOV -. "throttle / freeze / reclaim / stop" .-> COMP
  GOV -. .-> K8S
  GOV -. .-> LOAD
  GOV -. .-> VM
  SD -. "last resort" .-> LOAD
  APP --> SCTLD
  APP --> BMC
  APP --> BCMM
  APP --> JIRA
  APP --> K3D
  PROM --> GPUX
  PROM --> SLURMD
  GRAF --> PROM
  USERS --> SCTLD
  TBOT --> JIRA
  CHAOS --> BMC
  CHAOS --> GPUX
  CHAOS --> KWOK
  CHAOS --> SLURMD
  KWOK --- K3D
  SCTLD --- SLURMD
  FVM --- SCTLD
```

macOS is deliberately **not** in this diagram: it runs as an external agent (section 8).

## 4. Component decisions (what, why, alternatives)

### 4.1 Slurm: containerized, not VMs
- Run `slurmctld` + `slurmrestd` in core; `slurmdbd` + MariaDB only in profiles that test accounting/QOS.
- One container per fake compute node, all from **one shared base image** (shared file-backed pages). Community projects such as `giovtorres/slurm-docker-cluster` already use this pattern (controller, DB, and `c1`/`c2` `slurmd` containers under Docker Compose); use it as a starting point rather than writing from scratch.
- Container-friendly `slurm.conf` starting points (validate on your Slurm version): `ProctrackType=proctrack/linuxproc`, `TaskPlugin=task/none`, fake `RealMemory`, and GPUs declared as GRES by count (`Name=gpu Type=h200 Count=8`, no device files) so GPU partitions behave like a DGX/HGX fleet.
- Jobs are synthetic (`sleep`, `stress-ng` with capped memory), never real training.
- Optional later optimization: build Slurm with multiple-slurmd support to host many logical nodes in one container if per-container overhead matters.

### 4.2 Kubernetes: one real mini-cluster + KWOK for scale
- Replace the multi-VM Vagrant K8s with **k3d or kind**: 1 control-plane + 1 real worker (for real pods, real metrics).
- Add **KWOK (Kubernetes WithOut Kubelet)** for scale and faults: it simulates node and pod lifecycles against the real API server, with a very small footprint (the project advertises 1k nodes / 100k pods on a laptop). Fake GPU nodes advertise `nvidia.com/gpu` capacity; flipping a node to `NotReady` is a one-line fault.
- Constraint: kind/k3d create their node containers themselves, so don't assume you can set a cgroup parent for them. The governor addresses them by container name/label through the Docker API.

### 4.3 BMC: Redfish farm
- Build **`bmc-farm`**: a single process serving N virtual Redfish endpoints (per-port or per-virtual-host), each with inventory, power state, thermals, fans, sensors and an event log. Faults are injected via a control API (`/sim/inject`).
- Reference implementations: DMTF Redfish mockups for static trees; **`sushy-tools`** (OpenStack) offers a static responder and a *virtual* Redfish BMC backed by libvirt. Use sushy-tools' libvirt-backed emulator **only for the one fidelity VM** so power on/off really controls a VM.
- Add an IPMI facade later only if the app needs it.

### 4.4 BCM (NVIDIA Base Command Manager): API-compatible mock
- A real BCM head node is licensed, heavy software; don't try to run one here.
- Build **`bcm-mock`** implementing only the surface the app calls. NVIDIA documents the CMDaemon **REST API** (`/rest/v1/monitoring/...` entities, measurables, usage) and the **`pythoncm`** Python API (nodes, power status, monitoring data); mirror whichever the app consumes, with recorded real responses as fixtures where you have them.
- Keep the mock data-driven (YAML fixtures + the same `sim.yaml` node list) so BMC, BCM, Slurm and Prometheus always agree on node names.

### 4.5 Jira: three modes behind one switch
Note: Jira Cloud cannot be self-hosted, and Atlassian ended Jira Server support in February 2024; self-managed Jira today means Data Center (licensed, JVM-heavy). So decide per mode:

| Mode | RAM | When |
|---|---|---|
| `mock` (default) | ~50-100 MB | Daily dev, CI, scenarios. FastAPI/WireMock implementing the Jira REST endpoints the app uses (issue create/transition/comment, search/JQL subset, webhooks), with failure modes (429, 503, slow). |
| `cloud` | 0 local | Contract testing against a real **Jira Cloud free site** (small-team free tier; check current limits). Real auth, real rate limits. |
| `dc` | ~3 GB+ | Only the `jira-real` profile, which suspends K8s + fidelity VM to pay for it. Keep heap at the recommended size; don't starve it. |

Put **Toxiproxy** in front of the Jira mode only (an HTTP path, where it is cheap) to inject latency/outages. Do **not** put it in the Slurm RPC path; use `tc netem` there (section 4.9).

### 4.6 Observability
- Prometheus (retention about 2d, 15-30s scrape, capped cardinality) + Grafana + Alertmanager. Dashboards provisioned from files so they're reproducible.
- **`gpu-exporter-farm`**: fake DCGM-style metrics (utilization, memory, temp, power, XID/ECC counters) for every simulated GPU node, controllable by scenarios.
- The sim's own health (governor metrics) is exposed separately so it survives if Prometheus is stopped.

### 4.7 Workload and people simulation
- **`user-sim`**: personas (ML trainer, MPI researcher, array-job student, interactive user) with accounts/QOS, Poisson arrivals, a daily rhythm, and failure behavior (bad scripts, OOM jobs, cancellations). Optional: replay a Standard Workload Format trace.
- **`ticket-bot`**: fake support engineers that pick up tickets the app creates, comment, transition and resolve them on realistic delays, including "customer" tickets the app should triage.
- Submit through `slurmrestd` and the CLI so both integration paths of the app are exercised.

### 4.8 Fidelity VM (the single real VM)
- One Vagrant/libvirt Ubuntu VM, **on demand only**, for what containers can't prove: real kernel counters, systemd units, sshd, real `node_exporter`, power control via sushy-tools, and the app's Linux agent install path.
- Prefer **libvirt/KVM** (`vagrant-libvirt`) over VirtualBox: KVM supports memory ballooning and KSM page merging, and `virsh managedsave` can fully release its RAM.
- Hard-cap at 2 GB.

### 4.9 Storage, network and time simulation
- **Fake shared filesystems.** No NFS/Lustre servers. Emit static `node_exporter` textfile metrics (`node_filesystem_size_bytes`, `node_filesystem_free_bytes`) for dummy mounts such as `/mnt/lustre`; scenarios like `disk-full` just change the numbers.
- **tmpfs for ephemeral state.** MariaDB (Slurm accounting) and the Prometheus TSDB live on a capped tmpfs (about 200 MB each). State need not survive a host reboot, and this avoids IO saturation next to 30+ containers.
- **Network chaos with `tc netem`.** Latency, loss and partitions are applied on the Docker bridge networks between `core` and `mocks`/`compute`; no proxy process, no extra RAM.
- **Time dilation.** `time_dilation` in `sim.yaml` scales synthetic job durations and arrival rates (inversely) in `user-sim` and `scenario-engine`, so a 24 h queue pattern can run in minutes.

## 5. Memory contract (planning estimates; measure in Phase 0)

Host reserve (OS, desktop, IDE, browser): **about 3 GB**, never governed. Page cache and bursts need headroom, so target **at most about 9 GB** for the sim.

| Component | Est. RAM | Priority |
|---|---|---|
| governor + simctl + Web UI (< 50 MB) | 0.15 GB | P0 (protected) |
| Prometheus + Alertmanager + Grafana | 0.7 GB | P1 |
| slurmctld + slurmrestd | 0.2 GB | P1 |
| slurmdbd + MariaDB (small buffer pool) | 0.2 GB | P2 (profile-gated) |
| App under test | 1.0 GB (budget) | P1 |
| bmc-farm + bcm-mock + jira-mock + gpu-exporter-farm | 0.3 GB | P2 |
| k3d/kind (1 server + 1 agent) + KWOK | 1.2 GB | P3 |
| slurmd containers | ~60 MB each (16 nodes about 1 GB) | P4 |
| user-sim + ticket-bot + scenario-engine | 0.15 GB | P5 |
| Fidelity VM | 2.0 GB cap | P6 |
| Jira DC (jira-real only) | 3.0 GB+ | P6 |

**Profiles**

| Profile | What's on | Est. sim total |
|---|---|---|
| `min` | core, mocks, 4 slurmd, scenarios | ~3 GB |
| `std` | `min` + accounting, 16 slurmd, k3d + KWOK, load sims | ~6 GB |
| `full` | `std` + 32 slurmd, 200 KWOK nodes, fidelity VM | ~8.5 GB |
| `jira-real` | `std` minus k3d, plus Jira DC | ~7.5 GB |
| `mac-agent` | `min` + remote macOS agent (external) | ~3 GB local |

`simctl up --profile X` runs a **pre-flight**: `MemAvailable` minus the profile's declared budget must stay above the safety floor, otherwise it refuses and suggests a smaller profile.

## 6. The governor (outside monitoring and control)

### 6.1 Placement and protection
- Runs **on the host**, not in a container or VM: a systemd service with `OOMScoreAdjust=-900`, a `MemoryLow=` reservation, `Restart=always`, and its own slice. It talks to Docker (API), libvirt (`virsh`), systemd and cgroupfs.
- Compose services join per-priority slices with `cgroup_parent: hpcsim-<class>.slice` (needs Docker's systemd cgroup driver on cgroup v2). Resources it can't parent (kind/k3d nodes, the VM) are addressed by name through the Docker API or libvirt `memtune`.

### 6.1a Race-condition safeguards
Above roughly 30% PSI the kernel struggles to hand out new memory, so the governor must not need any:
- Lock all pages at startup (`mlockall(MCL_CURRENT | MCL_FUTURE)`, e.g. `os.mlockall()`), so it is never swapped out.
- Pre-allocate the L3/L4 execution path (open cgroup file handles, Docker/libvirt clients, command buffers) at startup; mitigations then perform no dynamic allocation.
- Listen on a Unix socket (`/run/hpcsim/gov.sock`) for commands from the unprivileged Web UI backend (section 7.1).

### 6.2 Signals
- `MemAvailable`, swap-in/out rate, and **PSI** (`/proc/pressure/memory`, per-cgroup `memory.pressure`): PSI measures time lost to memory stalls, which is a better "about to hurt" signal than raw usage.
- Per-group `memory.current`, `memory.events` (`high`/`max`/`oom_kill` counters) vs declared budgets.
- CPU and IO PSI as secondary signals.

### 6.3 Escalation ladder

| Level | Trigger (starting values, tune them) | Action | Targets |
|---|---|---|---|
| L0 normal | PSI some avg10 < 5% and MemAvailable > 2.5 GB | none | n/a |
| L1 warn | PSI some > 10% or MemAvailable < 2.0 GB | emit event/annotation; stop new job/ticket arrivals | P5 |
| L2 throttle | PSI some > 20% for 10 s | lower `memory.high` on low classes; slow `user-sim`; scale slurmd count down | P5, P4 |
| L3 freeze + reclaim | PSI some > 30% for 10 s | `docker pause` / `cgroup.freeze`, then `memory.reclaim` (kernel 5.19+) to push pages to zram/swap | P6, P5, P4 |
| L4 stop | PSI **full** > 5% or MemAvailable < 1.0 GB | `docker stop`; `virsh managedsave` the VM (frees its RAM) | P6, then P5, P4, P3 |
| L5 emergency | still degrading after L4 | stop the whole profile except P0/P1 | all but P0/P1 |

Key nuance: **pausing/freezing does not release RAM**, it only stops CPU use. Freeze is a cheap, instantly reversible step; actual relief comes from `memory.reclaim` (to compressed swap) or a real stop/save.

### 6.4 Behavior rules
- **Hysteresis:** escalate fast, de-escalate slowly (calm for 60 s before each step down), minimum dwell time per level.
- **Priority classes:** P0 governor/panel, P1 core + app, P2 mocks, P3 K8s, P4 compute, P5 load, P6 VM/Jira DC. Lowest priority goes first.
- **Reversible first, destructive last,** and every action is logged and visible in Grafana as an annotation.
- **Admission control:** `simctl up` and scenarios that start things must ask the governor first.
- **Backstops:** kernel `memory.max` per container as the hard cap; **systemd-oomd** on a sacrificial slice (`ManagedOOMMemoryPressure=kill`) so the kernel OOM killer is the last resort, not the first. oomd needs cgroup v2 and PSI.
- **Dry-run mode** (`governor --dry-run`) logs what it would do, for threshold tuning.

### 6.5 Host tuning (one-time, scripted by `simctl doctor`)
- Confirm cgroup v2 and PSI (`cat /proc/pressure/memory`).
- **zram** swap (zstd, about 25-50% of RAM), low-ish `vm.swappiness`, THP set to `madvise`.
- Docker: `max-size` log rotation, `--pids-limit`, per-container `--memory` and `--memory-reservation`.
- Enable KSM only if you use several similar VMs (it merges QEMU memory; it won't help ordinary containers).
- Slim defaults: MariaDB `innodb_buffer_pool_size` small, Grafana analytics/plugins off, Prometheus retention short.

## 7. One-click UX and control

```bash
simctl doctor                       # host checks + tuning suggestions
simctl up --profile std             # pre-flight, then bring everything up
simctl status                       # profile, per-class memory vs budget, governor level
simctl top                          # live table: groups, PSI, level, actions taken
simctl scale compute 24             # change slurmd count (governor-approved)
simctl jira mode mock|cloud|dc
simctl scenario list
simctl scenario run gpu-xid-storm   # trigger an issue
simctl workload run ml-peak --duration 30m
simctl down
```

The **Web UI** (section 7.1) mirrors the CLI. Desktop launcher / `make demo` runs `simctl up --profile std`.

### 7.1 Web UI

Budget: **< 50 MB** host RAM. No heavy JS frameworks.

| Layer | Choice |
|---|---|
| Backend | FastAPI (async, Python) |
| Frontend | HTMX + Tailwind CSS (HTML over the wire, no client build step) |
| Live data | Server-Sent Events, 1 s updates |

**Privilege separation.**

| Component | Privilege | Communication | Responsibility |
|---|---|---|---|
| Governor daemon | `root` | Listens on Unix socket `/run/hpcsim/gov.sock` | Executes scale/freeze/stop actions, monitors PSI |
| Web UI backend | `hpcsim` (restricted) | JSON over the Unix socket | Serves HTML, auth, translates clicks into socket commands |

A compromise of the Web UI can only issue the pre-approved commands the socket accepts; it never gets root.

**Modules.**
1. **Cluster Canvas:** visual map of the `sim.yaml` topology (K8s nodes, Slurm nodes, BMCs) with right-click node faults (*Set K8s NotReady*, *Inject GPU XID*, *Drain slurmd*).
2. **Governor Dashboard:** SSE sparklines for `MemAvailable` and PSI, a color-coded level banner (L0 green, L2 yellow, L4 red) and a scrolling audit log of governor actions.
3. **Scenario Orchestrator:** browse and run scenarios from `scenarios/` with Play, Stop and Fast-Forward, plus a timeline of upcoming injection steps.
4. **Profile & Budget Manager:** form editor for `sim.yaml`; sliders for node/GPU counts feed a projected PSS memory calculator, and deployment is blocked if the budget exceeds host safety limits.

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

Starter catalog: `node-down`, `gpu-xid-storm`, `ecc-uncorrectable`, `thermal-throttle`, `fan-failure`, `psu-redundancy-lost`, `slurm-drain-storm`, `job-storm`, `oom-jobs`, `queue-backlog`, `k8s-node-notready`, `pod-crashloop`, `network-partition` (`tc netem`), `disk-full` (fake metrics), `jira-outage` / `jira-429` (via Toxiproxy), `ticket-storm`, `bcm-api-timeout`.

## 8. macOS testing (read this before planning)

- Apple's license permits macOS in a VM **only on Apple hardware**. Projects such as `dockur/macos` and Docker-OSX state this explicitly, and Docker-OSX has been the subject of a takedown request from Apple. Running macOS under KVM on this Ubuntu PC would violate the license, so **it's not a recommended or supported part of this design**.
- Practical alternative: treat macOS as an **external agent target**:
  1. a real Mac (even a spare Mac mini) on the LAN running the app's macOS agent, reporting to the cluster services on this host;
  2. a rented Mac (hosted Mac mini or cloud Mac instance) used on demand for performance runs;
  3. a hosted macOS CI runner for automated regression.
- Even if it were allowed, a 4-8 GB macOS VM would not coexist with the `std`/`full` profiles on 16 GB, so the `mac-agent` profile stays `min`-sized locally.
- Agent-side measurements (CPU, memory, wakeups, battery impact) come from the Mac itself; the sim only provides the "cluster" it talks to.

## 9. Repository layout

```
opti-sim/
├── README.md  SPEC.md  CLAUDE.md
├── sim.yaml                  # single source of truth: nodes, GPUs, users, partitions, profile budgets
├── profiles/                 # min.yaml std.yaml full.yaml jira-real.yaml mac-agent.yaml
├── simctl/                   # Python (Typer): up/down/scale/scenario/doctor/top
├── governor/                 # Python daemon: signals, ladder, actions, metrics, dry-run
├── systemd/                  # hpcsim-*.slice, hpcsim-governor.service, oomd drop-ins
├── images/
│   ├── node-base/            # shared base for all slurmd containers
│   └── slurm-ctl/ ...
├── components/
│   ├── bmc-farm/  bcm-mock/  jira-mock/  gpu-exporter-farm/
│   └── user-sim/  ticket-bot/  scenario-engine/
├── webui/                    # FastAPI + HTMX + Tailwind (runs as `hpcsim` user)
├── compose/                  # static + generated compose files (cgroup_parent per class)
├── k8s/                      # k3d config, KWOK node/stage templates
├── fidelity-vm/Vagrantfile   # vagrant-libvirt, 2 GB cap, on-demand
├── observability/            # prometheus.yml (generated targets), dashboards, alert rules
├── scenarios/                # *.yaml
└── docs/adr/                 # decision records
```

## 10. Migration from what you have

| Existing | Becomes |
|---|---|
| Vagrant compute node | slurmd containers (`node-base`); keep one real VM only as the fidelity node |
| Vagrant Grafana node | Grafana + Prometheus containers in `core` |
| Vagrant K8s nodes | k3d/kind (1+1) + KWOK fake nodes |
| Jira container | `jira-mock` by default; the existing container becomes the `dc` mode of the `jira-real` profile |

## 11. Implementation roadmap

| Phase | Deliverable | Exit test |
|---|---|---|
| 0 Baseline | Measure idle + loaded RAM of each current piece (`docker stats`, `smem`, `virsh dommemstat`); fill real numbers into section 5 | Budget table replaced with measured values |
| 1 Governor | Host tuning script, slices, governor with dry-run and the ladder, `simctl top` | A synthetic memory hog gets throttled, frozen, then stopped in order, and recovers |
| 2 Slurm | `node-base`, ctld + restd, N slurmd from `sim.yaml`, Prometheus + Grafana | `simctl scale compute 16` works; app sees the nodes |
| 3 Mocks | bmc-farm, bcm-mock, gpu-exporter-farm, jira-mock + Toxiproxy | App's BMC/BCM/Jira integrations pass against mocks |
| 4 People + chaos | user-sim, ticket-bot, scenario engine, 5 starter scenarios | `gpu-xid-storm` produces tickets end to end |
| 5 Kubernetes | k3d + KWOK, fake GPU nodes, node-fault scenarios | App shows mixed real/fake K8s nodes |
| 6 Fidelity VM | vagrant-libvirt VM + sushy-tools power control | Redfish power-off really stops the VM |
| 7 Web UI + profiles | Web UI (canvas, dashboard, orchestrator, budget manager), profile pre-flight, `jira-real` swap | One click from cold start to a populated, busy cluster |
| 8 macOS + CI | External Mac agent profile; headless scenario runs with assertions | Scenario suite runs unattended |

## 12. Open decisions (they change the build)

1. **How does the app read Slurm?** CLI (needs a login-style node with Slurm client tools) vs `slurmrestd` (HTTP). The sim supports both, but this decides what must be fully faithful.
2. **BCM surface:** CMDaemon REST, `pythoncm`, or the JSON API? Pick the one the app calls so `bcm-mock` stays small.
3. **Jira auth/features needed:** Cloud API tokens and webhooks vs DC; JQL depth; Service Management features. Decides how much `jira-mock` must implement and when `cloud` mode is required.
4. **Real GPUs:** if you ever attach a real GPU node, it becomes an external target like the Mac, outside the 16 GB budget.
5. **Mac access:** which of the three external options is realistic for you.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Mocks drift from real APIs | Contract tests; record real responses (Jira Cloud free site, a real BCM/BMC if you have one) as fixtures |
| Governor flaps or overreacts | Hysteresis, min dwell, dry-run tuning, reversible actions first |
| Freeze doesn't free RAM | Ladder pairs freeze with `memory.reclaim` and falls through to stop/save |
| Per-container overhead adds up at 32+ nodes | Shared base image, tiny limits, later multiple-slurmd-per-container |
| Estimates wrong | Phase 0 measurement gate before committing to profiles |
| Real Jira DC starves the host | Exclusive `jira-real` profile; governor treats it as P6 |

## 14. References researched

- Slurm in Docker Compose (controller, DB, `c1`/`c2` slurmd): https://github.com/giovtorres/slurm-docker-cluster
- KWOK, simulated K8s nodes at low footprint: https://github.com/kubernetes-sigs/kwok
- sushy-tools, static and libvirt-backed virtual Redfish BMC: https://docs.openstack.org/sushy-tools/latest/user/dynamic-emulator.html
- NVIDIA Base Command Manager developer docs (CMDaemon REST API, Python API): https://docs.nvidia.com/base-command-manager/product-manuals/10/developer/restapi and https://docs.nvidia.com/base-command-manager/product-manuals/developer/pythonapi
- Atlassian Jira Data Center container (2 GiB recommended, heap env vars): https://atlassian.github.io/data-center-helm-charts/containers/JIRA
- macOS licensing constraints for VMs: https://github.com/dockur/macos and https://github.com/sickcodes/Docker-OSX/blob/master/FAQ.md
- cgroup v2 memory control (`memory.high` / `memory.max` / PSI) and systemd-oomd: https://www.netdata.cloud/academy/diagnosing-linux-cgroups/ and https://lore.kernel.org/all/20230601183820.3839891-1-schatzberg.dan@gmail.com/
