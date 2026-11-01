# workbench Specification

Normative requirements for workbench. [README.md](README.md) gives the overview and [docs/architecture/](docs/architecture/README.md) the rationale and architecture; this file defines what must be built and how it is verified. Key words MUST / SHOULD / MAY follow RFC 2119.

## 1. Goals and non-goals

**Goals**
- G1. Simulate an HPC/AI cluster (Slurm, Kubernetes, BMC, BCM, Jira, Grafana) on a 16 GB Ubuntu host so the app under test can be exercised end to end.
- G2. Keep the whole simulation within a declared memory budget per profile and never let the host swap-thrash.
- G3. Make every fault and workload a named, repeatable scenario.
- G4. One-command start (`benchctl up`) and a Web UI mirroring the CLI.
- G5. Be the primary test target for a desktop HPC monitoring client: every endpoint such a client uses (SSH login node, Slurm, Grafana GPU metrics, Jira search, Redfish BMC) MUST behave like the real service, and [docs/guides/client-testing.md](docs/guides/client-testing.md) MUST list where each is and what is not built.

**Non-goals**
- Running real training jobs, real BCM, real GPUs or a real shared filesystem.
- Running macOS in a VM on this host (license). macOS is an external agent only.

## 2. Platform requirements

| ID | Requirement |
|---|---|
| P-1 | Host: Ubuntu, cgroup v2, PSI enabled (`/proc/pressure/memory`), kernel 5.19+ for `memory.reclaim`. |
| P-2 | Docker with the systemd cgroup driver; libvirt/KVM for the fidelity VM. |
| P-3 | `benchctl doctor` MUST verify P-1/P-2 and report fixes; it MAY apply host tuning (zram, swappiness, THP) with confirmation. |

## 3. Configuration: `sim.yaml`

`sim.yaml` is the single source of truth. It MUST define: nodes (count, GPUs per node, GPU type), users/QOS/partitions, `time_dilation` (float, default 1.0), Jira mode, and per-profile memory budgets. All generated artifacts (compose files, `slurm.conf`, Prometheus targets, BMC and BCM inventories) MUST derive from it so node names agree everywhere.

## 4. Components

| ID | Component | Requirements |
|---|---|---|
| C-1 | Slurm | `slurmctld` + `slurmrestd` in core; `slurmdbd`/MariaDB only in accounting profiles; N `slurmd` containers from one shared base image; GPUs as count-based GRES; jobs are synthetic. |
| C-2 | Kubernetes | k3d/kind with 1 server + 1 real agent, plus KWOK fake nodes (incl. `nvidia.com/gpu` capacity). Governor addresses nodes by container name/label. |
| C-3 | bmc-farm | One process serving N Redfish endpoints (each node also has its own address 10.148.x.y:443 through a forwarder container that can be switched off per node, ADR 0018) with inventory, power, thermals, fans, sensors, event log; fault injection via `/sim/inject`. Also standard Redfish over TLS on 127.0.0.1:8443 (`/redfish/v1`, node chosen by the `Host` header, network alias `bmc-<node>`, session login, collections, firmware, accounts; ADR 0012). |
| C-4 | bcm-mock | Implements only the BCM surface the app calls (CMDaemon REST or `pythoncm`), data-driven from fixtures and `sim.yaml`. For SSH-based clients (they run `cmsh` on the head node over an SSH hop) the surface is a fake read-only `cmsh` on the login node (ADR 0013, issue 66): `cmsh -c "device; list"`, `device; status`, `category; list`, `jobqueue; list` and `monitoring; healthchecks; list`, state taken from bcm-mock, anything mutating refused. |
| C-5 | jira-mock | Jira REST subset (issue create/transition/comment, JQL subset, webhooks) with failure modes (429, 503, slow); per-transition required-field rules that refuse a transition with a per-field error, as real workflow validators do. Modes: `mock` (default), `cloud`, `dc`. Also the calls an HPC monitoring client makes: POST search (`/search/jql` and `/search`), `myself`, `serverInfo`, `field`, `project`, and JQL with grouping, OR and text search (ADR 0005). |
| C-6 | gpu-exporter-farm | Fake DCGM metrics (util, memory, temp, power, XID/ECC) per simulated GPU, scenario-controllable. Series carry the real DCGM labels (`Hostname`, `gpu`, `UUID`, `device`, `modelName`) next to `node`, and `FB_USED` + `FB_FREE` add up to the GPU type's framebuffer. |
| C-7 | Observability | Prometheus (about 2 d retention) + Grafana + Alertmanager with file-provisioned dashboards. Governor metrics exposed independently of Prometheus. |
| C-8 | user-sim / ticket-bot | Personas with Poisson arrivals and failure behavior; fake engineers working Jira tickets on realistic delays. Submission via `slurmrestd` and CLI. |
| C-9 | scenario-engine | Runs YAML scenarios (steps with `at`, `target`, `do`; optional `expect` assertions). |
| C-10 | Fidelity VM | Single on-demand libvirt Ubuntu VM, hard cap 2 GB, power via sushy-tools. |
| C-11 | Storage sim | No NFS/Lustre servers. Static `node_exporter` textfile metrics for fake mounts. MariaDB and Prometheus TSDB on capped tmpfs (about 200 MB). |
| C-12 | Network chaos | `tc netem` on Docker bridge networks for latency, loss, partition. Toxiproxy only on the Jira HTTP path. |
| C-14 | Login node | One `login` container (`sshd`, Slurm client tools, key login only, no root, TCP forwarding on, simulated users from `sim.yaml`) on 127.0.0.1:2222, unprivileged and memory-limited, keys generated into `compose/generated/secrets/ssh`; for SSH-based clients that run `squeue`/`sinfo`/`sacct` over SSH (ADR 0011). |
| C-13 | Time dilation | `time_dilation` scales job durations and arrival rates inversely in user-sim and scenario-engine. |
| C-15 | Node health check | `slurmd` runs `images/node-base/nhc` every 30 s (`HealthCheckProgram`): it also runs as Slurm `Prolog` and `Epilog` (an unhealthy node fails the job, which requeues). It drains the node (`reason=NHC: ...`) when a GPU is missing against the GRES, reports an XID, an InfiniBand port is down or a NIC is flapping (the exporter farm models both), and resumes only a drain it made. When Slurm takes a node out of service, `slurmctld`'s `node_watch.py` files or comments a Jira ticket (issues 143, 160). `benchctl up` restarts `slurmctld` when `slurm.conf` changes. |

## 4a. Component interfaces (summary)

- Governor control socket: `/run/workbench/gov.sock`, JSON requests, a fixed allow-list of commands (status, scale, freeze, thaw, stop, start, set-profile, admit, apply-plan, node-state: a node name and `off`, `on` (freeze or thaw its container so Slurm detects the outage itself), `down`, `drain` (with a short reason) or `resume`, W-4). Unknown commands MUST be rejected.
- bmc-farm: Redfish under `/redfish/v1/...` per endpoint (TLS :8443, ADR 0012; plain `/<node>/redfish/v1` on :8000); control API `POST /sim/inject`.
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
| GV-9 | Admission control: `benchctl up` and any scenario that starts workloads MUST be approved by the governor first. |
| GV-10 | Backstops: per-container `memory.max`; `systemd-oomd` on a sacrificial slice. |

## 6. Profiles and pre-flight

Profiles: `min`, `std`, `full`, `jira-real`, `mac-agent` ([architecture section 5](docs/architecture/04-memory-and-profiles.md)). `benchctl up --profile X` MUST refuse to start when `MemAvailable - profile budget` is below the safety floor and MUST suggest a smaller profile. Budgets are planning estimates until Phase 0 replaces them with measured values.

## 7. CLI (`benchctl`)

`doctor`, `up --profile`, `down`, `status`, `top`, `scale <group> <n>`, `jira mode <mock|cloud|dc>`, `scenario list|run`, `workload run`. All state-changing commands go through the governor.

## 8. Web UI

| ID | Requirement |
|---|---|
| W-1 | Backend Starlette (ADR 0008: importing FastAPI alone costs ~39 MB); frontend one static HTML page with vanilla JS and a hand-written stylesheet, no framework, no CDN, no build step (ADR 0007); total host RAM under 50 MB. |
| W-2 | Live telemetry via Server-Sent Events at 1 s intervals. |
| W-3 | Runs as the restricted `workbench` user and talks to the governor only through the Unix socket; it MUST NOT have root, Docker-socket or libvirt access of its own. |
| W-4 | Cluster view: topology from `sim.yaml` shown as a traffic diagram with every node and an entity list, with per-node fault actions (power off, overheat, fan failure, GPU XID, clear; Set K8s NotReady and Drain slurmd need Docker and run through the scenario CLI). |
| W-5 | Governor panel: MemAvailable and PSI sparklines, color-coded level banner, audit log; reachable from the top strip, and a level of L2 or higher or an offline governor is also listed under Needs attention. |
| W-6 | Scenario Orchestrator: browse, Play, Stop, Fast-Forward, timeline of upcoming steps. |
| W-7 | Profile & Budget Manager: `sim.yaml` form editor, projected PSS memory calculator, deployment blocked when over budget. |
| W-8 | Authentication is required; default bind is localhost. |
| W-9 | Accessibility: status is never shown by colour alone; text contrast is at least 4.5:1 in light and dark; tap targets are at least 40 px; tables stack as cards under 640 px; the page has ARIA landmarks and a skip link; reduced motion is respected; nothing scrolls sideways at 390 px. |
| W-10 | One message per condition. When the stack or the governor is down the page shows a single notice and one "needs attention" entry, hides data that cannot exist (no red or empty placeholders), and names a recovery command that works in a supported mode (the start path never needs sudo). |
| W-11 | Live feeds (SSE, polling) run only while the page is visible, and animation stops at governor L3 and under reduced motion (ADR 0009). |
| W-12 | One screen: at 1280 x 800 with six nodes the dashboard needs no page scrolling (traffic diagram and entity list side by side; they stack under 1100 px). Long lists are bounded inside their own panel and filterable, Scenarios, Plan and Governor are sheets opened from the top strip, and a node opens in place of the list so it never covers the traffic diagram. A Connections view gives each part's address, a command to try and the path of its credential (never the secret). |
| W-13 | Applying a plan from the page goes only through the governor (`apply-plan`: bounded counts and allow-listed parts, no free-form arguments), only at L0-L1 (GV-8), and needs the governor's `--workdir`; otherwise the page says why in one line (ADR 0017). |
| W-14 | **⇩ Export** in the top strip downloads the client profile file for the cluster on disk (saved selection, else the profile `up` started), read fresh per request; no Docker access (W-3), no secret in the file, one message when the client format ID is not set locally. `benchctl export profile` lists the running containers instead (Docker), and only a node that is up gets a BMC target. |
| W-15 | **⚙ Settings** is the one place for options that change what runs and its memory: node counts and the optional parts (accounting, Jira, BMC, BCM, scenarios, load). The options come from one registry (`benchctl/workbench.py` `PART_INFO`/`PARTS`, served at `/api/settings` with each part's memory limit and whether it is built), so a new option is a registry entry, not page code. Applying is W-13. |

## 9. Non-functional requirements

- NF-1. A synthetic memory hog MUST be throttled, frozen, then stopped in ladder order, and the system MUST recover without a host OOM.
- NF-2. Mocks MUST be covered by contract tests against recorded real responses where available.
- NF-3. Everything is reproducible from the repository plus `sim.yaml`; no manual per-node setup.
- NF-4. The repository is public: tracked files, commit messages, issues and pull requests MUST NOT contain personal data (home-directory paths, local usernames, personal emails, hostnames), tokens or keys. Local-only tooling (`.claude/`, whose skills are symlinks into a home directory, and `graphify-out/`) is gitignored and never committed. `scripts/check-privacy.sh` enforces this in the git hooks (`scripts/install-hooks.sh`), in `validate.sh` and before every push; commits are authored with the GitHub noreply address.
- NF-5. Starting, building and stopping the simulation MUST NOT need sudo (ADR 0010); root is only for the optional boot-time governor service.
- NF-6. When the governor refuses an action (admission at L2 and above, GV-9) the tool MUST say so and why, and tests and scripts MUST NOT report that as a defect in the code under test.
- NF-7. Every commit is authored and committed with the repository owner's (or an allow-listed contributor's) real GitHub noreply identity; GitHub credits by the numeric ID in the address, so a placeholder identity MUST be refused before it is committed or pushed (`scripts/check-identity.sh`, run by the pre-commit and pre-push hooks and `validate.sh`).
- NF-8. Every container has a fixed private IP from one plan (`benchctl/addressing.py`, ADR 0018): compute nodes 10.141.0.N, GPU nodes 10.141.100.N, login 10.141.255.254, services 10.141.255.0/24. Clients use those at standard ports; 127.0.0.1 with the published ports is the fallback. `benchctl up` MUST refuse, with one message, an existing network with another subnet or a host route that overlaps the plan.

## 10. Acceptance (per roadmap phase)

Exit tests are listed in [architecture section 11](docs/architecture/09-roadmap-decisions-risks.md) and are the acceptance criteria for each phase. Phase 0 (measurement) gates all budget numbers.

## 11. Open decisions

See [architecture section 12](docs/architecture/09-roadmap-decisions-risks.md) (Slurm access path, BCM surface, Jira features, real GPUs, Mac access). Each MUST be resolved before the dependent component is implemented.

Not built yet (so no requirement above is satisfied by them): W-7 Profile and Budget Manager; the user simulator and ticket bot; Slurm job dots on the traffic diagram; Teleport access (issue #39, which waits on its open decisions); Kubernetes and the fidelity VM.

Also open: the governor serves control commands under the same lock that executes a plan, so `status` waits for the whole plan. Measured with the rootless executor, a container stop costs about 175 ms and a pause about 28 ms (about 2 s for 12 containers); decide whether control commands need a bound before the `full` profile is relied on (issue #53).
