# opti-sim Specification

Normative requirements for opti-sim. [README.md](README.md) gives the overview and [docs/architecture/](docs/architecture/README.md) the rationale and architecture; this file defines what must be built and how it is verified. Key words MUST / SHOULD / MAY follow RFC 2119.

## 1. Goals and non-goals

**Goals**
- G1. Simulate an HPC/AI cluster (Slurm, Kubernetes, BMC, BCM, Jira, Grafana) on a 16 GB Ubuntu host so the app under test can be exercised end to end.
- G2. Keep the whole simulation within a declared memory budget per profile and never let the host swap-thrash.
- G3. Make every fault and workload a named, repeatable scenario.
- G4. One-command start (`simctl up`) and a Web UI mirroring the CLI.

**Non-goals**
- Running real training jobs, real BCM, real GPUs or a real shared filesystem.
- Running macOS in a VM on this host (license). macOS is an external agent only.

## 2. Platform requirements

| ID | Requirement |
|---|---|
| P-1 | Host: Ubuntu, cgroup v2, PSI enabled (`/proc/pressure/memory`), kernel 5.19+ for `memory.reclaim`. |
| P-2 | Docker with the systemd cgroup driver; libvirt/KVM for the fidelity VM. |
| P-3 | `simctl doctor` MUST verify P-1/P-2 and report fixes; it MAY apply host tuning (zram, swappiness, THP) with confirmation. |

## 3. Configuration: `sim.yaml`

`sim.yaml` is the single source of truth. It MUST define: nodes (count, GPUs per node, GPU type), users/QOS/partitions, `time_dilation` (float, default 1.0), Jira mode, and per-profile memory budgets. All generated artifacts (compose files, `slurm.conf`, Prometheus targets, BMC and BCM inventories) MUST derive from it so node names agree everywhere.

## 4. Components

| ID | Component | Requirements |
|---|---|---|
| C-1 | Slurm | `slurmctld` + `slurmrestd` in core; `slurmdbd`/MariaDB only in accounting profiles; N `slurmd` containers from one shared base image; GPUs as count-based GRES; jobs are synthetic. |
| C-2 | Kubernetes | k3d/kind with 1 server + 1 real agent, plus KWOK fake nodes (incl. `nvidia.com/gpu` capacity). Governor addresses nodes by container name/label. |
| C-3 | bmc-farm | One process serving N Redfish endpoints with inventory, power, thermals, fans, sensors, event log; fault injection via `/sim/inject`. |
| C-4 | bcm-mock | Implements only the BCM surface the app calls (CMDaemon REST or `pythoncm`), data-driven from fixtures and `sim.yaml`. |
| C-5 | jira-mock | Jira REST subset (issue create/transition/comment, JQL subset, webhooks) with failure modes (429, 503, slow); per-transition required-field rules that refuse a transition with a per-field error, as real workflow validators do. Modes: `mock` (default), `cloud`, `dc`. |
| C-6 | gpu-exporter-farm | Fake DCGM metrics (util, memory, temp, power, XID/ECC) per simulated GPU, scenario-controllable. Series carry the real DCGM labels (`Hostname`, `gpu`, `UUID`, `device`, `modelName`) next to `node`, and `FB_USED` + `FB_FREE` add up to the GPU type's framebuffer. |
| C-7 | Observability | Prometheus (about 2 d retention) + Grafana + Alertmanager with file-provisioned dashboards. Governor metrics exposed independently of Prometheus. The Prometheus datasource has the fixed uid `prometheus`; `simctl up` also makes a read-only (Viewer) service-account token in `compose/generated/secrets/grafana_viewer_token` for monitoring clients. |
| C-8 | user-sim / ticket-bot | Personas with Poisson arrivals and failure behavior; fake engineers working Jira tickets on realistic delays. Submission via `slurmrestd` and CLI. |
| C-9 | scenario-engine | Runs YAML scenarios (steps with `at`, `target`, `do`; optional `expect` assertions). |
| C-10 | Fidelity VM | Single on-demand libvirt Ubuntu VM, hard cap 2 GB, power via sushy-tools. |
| C-11 | Storage sim | No NFS/Lustre servers. Static `node_exporter` textfile metrics for fake mounts. MariaDB and Prometheus TSDB on capped tmpfs (about 200 MB). |
| C-12 | Network chaos | `tc netem` on Docker bridge networks for latency, loss, partition. Toxiproxy only on the Jira HTTP path. |
| C-13 | Time dilation | `time_dilation` scales job durations and arrival rates inversely in user-sim and scenario-engine. |

## 4a. Component interfaces (summary)

- Governor control socket: `/run/hpcsim/gov.sock`, JSON requests, a fixed allow-list of commands (status, scale, freeze, thaw, stop, start, set-profile). Unknown commands MUST be rejected.
- bmc-farm: Redfish under `/redfish/v1/...` per endpoint; control API `POST /sim/inject`.
- Scenario file: YAML, see [architecture section 7](docs/architecture/06-ux-and-scenarios.md).

## 5. Governor

| ID | Requirement |
|---|---|
| GV-1 | Runs on the host as a systemd service in its own slice: `OOMScoreAdjust=-900`, `MemoryLow=`, `Restart=always`. |
| GV-2 | Signals: `MemAvailable`, swap rate, memory PSI (system and per-cgroup), `memory.current`/`memory.events`, CPU/IO PSI as secondary. |
| GV-3 | Escalation ladder L0 to L5 as in [architecture section 6.3](docs/architecture/05-governor.md). Thresholds are configurable; the README values are defaults. |
| GV-4 | Hysteresis: escalate immediately, de-escalate only after 60 s calm, with a minimum dwell per level. |
| GV-5 | Priority classes P0 to P6; lowest priority is acted on first; reversible actions precede destructive ones. |
| GV-6 | Every action is logged and published as a Grafana annotation and to the Web UI audit log. |
| GV-7 | `--dry-run` logs intended actions without executing them. |
| GV-8 | The governor MUST lock its memory (`mlockall`) at startup and MUST pre-allocate the L3/L4 execution path so mitigations perform no dynamic allocation. The rootless mode (`--user-mode`, ADR 0010, used when no system service runs) is best effort and cannot meet the lock guarantee. |
| GV-9 | Admission control: `simctl up` and any scenario that starts workloads MUST be approved by the governor first. |
| GV-10 | Backstops: per-container `memory.max`; `systemd-oomd` on a sacrificial slice. |

## 6. Profiles and pre-flight

Profiles: `min`, `std`, `full`, `jira-real`, `mac-agent` ([architecture section 5](docs/architecture/04-memory-and-profiles.md)). `simctl up --profile X` MUST refuse to start when `MemAvailable - profile budget` is below the safety floor and MUST suggest a smaller profile. Budgets are planning estimates until Phase 0 replaces them with measured values.

## 7. CLI (`simctl`)

`doctor`, `up --profile`, `down`, `status`, `top`, `scale <group> <n>`, `jira mode <mock|cloud|dc>`, `scenario list|run`, `workload run`. All state-changing commands go through the governor.

## 8. Web UI

| ID | Requirement |
|---|---|
| W-1 | Backend Starlette (ADR 0008: importing FastAPI alone costs ~39 MB); frontend one static HTML page with vanilla JS and a hand-written stylesheet, no framework, no CDN, no build step (ADR 0007); total host RAM under 50 MB. |
| W-2 | Live telemetry via Server-Sent Events at 1 s intervals. |
| W-3 | Runs as the restricted `hpcsim` user and talks to the governor only through the Unix socket; it MUST NOT have root, Docker-socket or libvirt access of its own. |
| W-4 | Cluster Canvas: topology from `sim.yaml` with per-node fault actions (Set K8s NotReady, Inject GPU XID, Drain slurmd). |
| W-5 | Governor Dashboard: MemAvailable and PSI sparklines, color-coded level banner, scrolling audit log. |
| W-6 | Scenario Orchestrator: browse, Play, Stop, Fast-Forward, timeline of upcoming steps. |
| W-7 | Profile & Budget Manager: `sim.yaml` form editor, projected PSS memory calculator, deployment blocked when over budget. |
| W-8 | Authentication is required; default bind is localhost. |
| W-9 | Accessibility: status is never shown by colour alone; text contrast is at least 4.5:1 in light and dark; tap targets are at least 40 px; tables stack as cards under 640 px; the page has ARIA landmarks and a skip link; reduced motion is respected; nothing scrolls sideways at 390 px. |
| W-10 | One message per condition. When the stack or the governor is down the page shows a single notice and one "needs attention" entry, hides data that cannot exist (no red or empty placeholders), and names a recovery command that works in a supported mode (the start path never needs sudo). |
| W-11 | Live feeds (SSE, polling) run only while the page is visible, and animation stops at governor L3 and under reduced motion (ADR 0009). |

## 9. Non-functional requirements

- NF-1. A synthetic memory hog MUST be throttled, frozen, then stopped in ladder order, and the system MUST recover without a host OOM.
- NF-2. Mocks MUST be covered by contract tests against recorded real responses where available.
- NF-3. Everything is reproducible from the repository plus `sim.yaml`; no manual per-node setup.
- NF-4. The repository is public: tracked files, commit messages, issues and pull requests MUST NOT contain personal data (home-directory paths, local usernames, personal emails, hostnames), tokens or keys. Local-only tooling (`.claude/`, whose skills are symlinks into a home directory, and `graphify-out/`) is gitignored and never committed. `scripts/check-privacy.sh` enforces this in the git hooks (`scripts/install-hooks.sh`), in `validate.sh` and before every push; commits are authored with the GitHub noreply address.
- NF-5. Starting, building and stopping the simulation MUST NOT need sudo (ADR 0010); root is only for the optional boot-time governor service.
- NF-6. When the governor refuses an action (admission at L2 and above, GV-9) the tool MUST say so and why, and tests and scripts MUST NOT report that as a defect in the code under test.

## 10. Acceptance (per roadmap phase)

Exit tests are listed in [architecture section 11](docs/architecture/09-roadmap-decisions-risks.md) and are the acceptance criteria for each phase. Phase 0 (measurement) gates all budget numbers.

## 11. Open decisions

See [architecture section 12](docs/architecture/09-roadmap-decisions-risks.md) (Slurm access path, BCM surface, Jira features, real GPUs, Mac access). Each MUST be resolved before the dependent component is implemented.

Not built yet (so no requirement above is satisfied by them): W-7 Profile and Budget Manager; the user simulator and ticket bot; Slurm job dots on the traffic diagram; Teleport access (issue #77, which waits on its open decisions); Kubernetes and the fidelity VM.

Also open: the governor serves control commands under the same lock that executes a plan, so `status` waits for the whole plan. Measured with the rootless executor, a container stop costs about 175 ms and a pause about 28 ms (about 2 s for 12 containers); decide whether control commands need a bound before the `full` profile is relied on (issue #102).
