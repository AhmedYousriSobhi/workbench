# Issue triage

Which open issues the repository already satisfies. Merging to `dev` does not close issues, so shipped ones were left open. Checked on 2026-11-01 against merged PRs, files in the tree and `docs/progress.yaml`; the live stack was not run for this.

| Verdict | Meaning | Count |
|---|---|---|
| shipped | the acceptance criteria are met by merged code or docs; the issue is closed with a comment | 117 |
| partial | built, with a named gap; stays open | 4 |
| not built | no code yet; stays open | 2 |
| unverified | not enough evidence in the tree; stays open until someone checks it live or by reading | 3 |

Titles use the current tool names where the old ones were renamed (#111). Issue numbers below 90 come from the repository's earlier history, so their evidence is a file or ADR, not a PR link.

## CLI

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #8 | Validation helper script run before every commit | shipped | scripts/validate.sh, scripts/verify.sh, tests/real/helpers.py |
| #11 | benchctl: doctor, preflight, status and diag bundle | shipped | benchctl/checks.py, diag.py, measure.py, compose.py, stack.py, machine.py |
| #12 | Phase 0: memory measurement tooling (benchctl measure) | shipped | benchctl/checks.py, diag.py, measure.py, compose.py, stack.py, machine.py |
| #13 | Compose generation from sim.yaml and profiles (benchctl compose) | shipped | benchctl/checks.py, diag.py, measure.py, compose.py, stack.py, machine.py |
| #18 | benchctl up/down/scale with governor admission, ordered startup and Grafana token provisioning | shipped | benchctl/checks.py, diag.py, measure.py, compose.py, stack.py, machine.py |
| #19 | Progress tracker: PROGRESS.md with generated flow charts, HTML view and how-to guide | shipped | benchctl/progress.py, docs/progress.yaml |
| #22 | Verification helpers: scripts/verify.sh and shared real-test helpers | shipped | scripts/validate.sh, scripts/verify.sh, tests/real/helpers.py |
| #44 | feat(check): initial machine check before building (scripts/check-machine.sh, benchctl check) | shipped | benchctl/checks.py, diag.py, measure.py, compose.py, stack.py, machine.py |
| #48 | feat(cli): one-command launcher: ./scripts/benchctl start / stop / status / open | shipped | benchctl/checks.py, diag.py, measure.py, compose.py, stack.py, machine.py |
| #80 | feat: benchctl ssh-key install/remove so clients that only read keys under ~/.ssh can log in | shipped | benchctl ssh-key in benchctl/cli.py |
| #90 | feat: one controller command, kubectl-style (benchctl <verb> <noun>), replacing the old CLI names | shipped | benchctl/cli.py single controller command (ADR 0015) |
| #91 | feat: export the running workbench as a client profile file (JSON import) | shipped | merged PR #95 and #134 (benchctl/export.py) |
| #92 | feat: choose the workbench to build (counts per kind), estimate resources first, then build | shipped | merged PR #97 (choose the workbench, benchctl/workbench.py) |
| #93 | feat: inspect any node: benchctl describe node <name> and a node detail view in the GUI | shipped | merged PR #96 (benchctl/describe.py) |
| #98 | chore: benchctl conventions from kubectl, Slurm and CLI guidelines (stderr, typo hints, deprecation notice, verb limits) | shipped | stderr errors, 'Did you mean' for a mistyped verb or value, docs/guides/benchctl-verbs.md; the old-name notice is moot after #111 |
| #111 | Remove the old tool names everywhere (benchctl and workbench) | shipped | merged PR #112 |
| #132 | feat: export the running cluster as a client profile, from the CLI and a button in the UI | shipped | merged PR #95 and #134 (benchctl/export.py) |
| #162 | verify.sh leaves the cluster on the std profile; keep the user's selection | shipped | merged PR #163 (scripts/lib-selection.sh) |
| #167 | chore: version 0.1.0 for the first release | shipped | merged PR #168 |

## Docs and wiki

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #23 | Getting-started user guide | shipped | docs/guides/getting-started.md |
| #45 | docs: split README into a short overview plus docs/ architecture pages, add build/run guide | shipped | docs/architecture/ split from README |
| #47 | docs(readme): add a hands-on 'Try it' walkthrough to the README | shipped | README.md sections 1-5 (start, poke, scenario, point your app, put it back), 83 lines |
| #51 | docs: remove stale root-only and two-tab wording | shipped | stale wording fixed in docs/manual/11-which-command.md and ADR 0009 (issue 177) |
| #59 | docs: user manual (book) and a 'get a shell on a node' step in the guide | shipped | docs/manual/ |
| #67 | chore: remove the monitoring app's lab names from docs and comments | shipped | git grep for the old lab names finds nothing in tracked files |
| #76 | feat: in-repo wiki for the tools, browsable from the web page | shipped | webui/wiki.py (/help/) |
| #77 | feat: publish the manual as the repository's GitHub Wiki, generated from docs/ | shipped | scripts/sync_wiki.py, .github/workflows/wiki.yml |
| #102 | Wiki: screenshots still show the old repository name | unverified | wiki screenshots still show the old name; needs a visual check |
| #113 | CI: publish the wiki automatically after each merge to dev | shipped | scripts/sync_wiki.py, .github/workflows/wiki.yml |
| #164 | docs: SPEC C-15 still says NIC flapping and IB checks are not built | shipped | merged PR #165 |

## Governor

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #5 | Governor skeleton: signals, ladder, hysteresis, dry-run | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #6 | Governor: mlockall, pre-allocated path, control socket, systemd unit | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #7 | Governor: real executors and group inventory | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #9 | Governor: verify root-only paths (Docker cgroups, systemd unit) and VM save | shipped | tests/real/test_root.py (root freeze/thaw recorded in docs/progress.yaml) |
| #10 | NF-1: real memory-hog test via per-cgroup signals and configurable thresholds | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #15 | Governor: continuous container security and limit-drift monitoring | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #16 | Governor: periodic inventory refresh and Grafana annotations (GV-6) | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #20 | Monitor the accepted slurmrestd unshare exception (decision A of #40) | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #21 | Governor admission (GV-9) and real scale/set-profile | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #25 | Install the governor as a standing systemd service | partial | scripts/install-governor.sh and systemd/ exist; the tracker keeps 'installed permanently' as partial because the sudo install is run by the owner |
| #26 | fix: /run/workbench is root-only, so --socket-gid users cannot reach gov.sock | shipped | governor/__main__.py --socket-gid |
| #28 | chore: lower the memory safety floor from 1.5 GB to 1 GB | shipped | sim.yaml safety_floor_gb: 1 |
| #30 | fix: pressure test looked for container cgroups in the wrong place; add benchctl wrapper | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #31 | fix: governor looks for container cgroups in the wrong place (systemd slice nesting) | shipped | governor/ modules (signals, ladder, planner, executors, memlock, control, watchdog, annotate, metrics); tests/real/test_root.py, test_pressure_real.py |
| #32 | docs: tracker records the passing root freeze/thaw test | shipped | tests/real/test_root.py (root freeze/thaw recorded in docs/progress.yaml) |
| #50 | feat: start and build without sudo (rootless governor mode) | shipped | governor --user-mode, scripts/benchctl-launch, ADR 0010 |
| #52 | ui: stale sudo hint when governor is offline; collapse repeated stack-down noise | shipped | Web UI banner says no sudo; install script is optional |
| #53 | docs: governor as built (reverse-engineered) with measured lock-hold time | shipped | docs/architecture/11-governor-as-built.md (lock-hold measured) |

## Jira

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #1 | Spec: jira-mock must model refused transitions | shipped | components/jira-mock/jira.py transition rules; SPEC C-5 |
| #63 | feat: jira-mock gaps for monitoring clients (search/jql, myself, field, serverInfo, auth) | shipped | components/jira-mock/jira.py (search/jql, myself, field, serverInfo) |
| #157 | Open a Jira ticket automatically when a node goes down or reports a GPU fault | shipped | merged PR #159 (fault tickets, background activity) |
| #158 | Background Jira activity: tickets change status, get comments and new tickets by themselves | shipped | merged PR #159 (fault tickets, background activity) |

## Kubernetes

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #2 | Port the Kubernetes test fixtures from the earlier lab | shipped | k8s/fixtures (the k3d/KWOK cluster itself is tracked as partial, not part of this issue) |

## Network and BMC

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #24 | bmc-farm: Redfish mock serving every node from one process | shipped | components/bmc-farm, ADR 0012; merged PR #134 |
| #62 | feat: Redfish-compatible bmc-farm (TLS, service root, collections, sessions) | shipped | components/bmc-farm, ADR 0012; merged PR #134 |
| #145 | fix: BMC connection refused on the standard Redfish port; export lists the BMC by default in the page | shipped | components/bmc-farm, ADR 0012; merged PR #134 |
| #146 | fix: exported BMC targets point at 127.0.0.1, which is the login node itself when the client goes through the jump host | shipped | components/bmc-farm, ADR 0012; merged PR #134 |
| #147 | design: an addressing plan for the simulated entities (distinct IPs instead of everything on 127.0.0.1) | shipped | merged PR #150, ADR 0018, benchctl/addressing.py |
| #148 | feat: fixed private subnet and static IPs for every container (client uses real addresses, not 127.0.0.1) | shipped | merged PR #150, ADR 0018, benchctl/addressing.py |
| #149 | feat: one small BMC container per node with its own IP on the ipmi network, enabled or disabled per node | shipped | merged PR #150, ADR 0018, benchctl/addressing.py |

## Node health and faults

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #141 | feat: node health check (NHC-style) that drains a GPU node on XID or a missing GPU | shipped | merged PR #134 (images/node-base/nhc) |
| #143 | feat: simulate NIC flapping and down InfiniBand ports for the node health check | shipped | merged PR #161 (IB and NIC checks, prolog/epilog) |
| #160 | Slurm-detected DOWN/drain files a Jira ticket; NHC prolog and epilog; IB and NIC checks | shipped | merged PR #161 (IB and NIC checks, prolog/epilog) |
| #170 | NHC checks: add, modify, remove from the CLI, by editing files, and from the GUI, with validation | shipped | merged PR #171 (benchctl/nhc.py, docs/guides/nhc-checks.md) |

## Observability and GPU

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #3 | Observability: textfile metrics generator and hpc-overview dashboard | shipped | components/gpu-exporter-farm/exporter.py (hpc_jobs_* series), observability/dashboards/hpc-overview.json, Grafana provisioning files |
| #64 | feat: GPU metrics parity for monitoring clients (DCGM_FI_DEV_FB_FREE, datasource UID, host label) | shipped | components/gpu-exporter-farm (real DCGM labels, FB_FREE) |

## Repo hygiene and release

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #4 | Fidelity VM: Vagrantfile with IP-detection retry | partial | fidelity-vm/ scaffold exists; not re-verified end to end (tracker partial) |
| #54 | chore: public-repo hygiene: privacy guard in git hooks, ignore local tooling, SECURITY and PR template | shipped | scripts/check-privacy.sh, .githooks/, SECURITY.md, PR template |
| #56 | chore: add a LICENSE (Apache-2.0) to the public repo | shipped | LICENSE-APACHE, LICENSE-MIT |
| #57 | Add the app logo (one paused node) as favicon and README header | shipped | docs/assets/logo.svg, README header |
| #58 | Add PNG fallback icons for browsers that ignore SVG favicons | shipped | docs/assets/favicon-16.png, favicon-32.png, apple-touch-icon.png |
| #78 | chore: definition of done for every feature (docs, wiki, user-friendliness, client test) | shipped | CLAUDE.md definition of done |
| #85 | chore: point docs and the tracker at the new repository (issue numbers, PR links) | shipped | merged PR #86 |
| #87 | chore: refuse commits with a wrong or placeholder git identity (incident: commits credited to an unrelated GitHub user) | shipped | merged PR #88 (scripts/check-identity.sh) |

## SSH, login and BCM

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #34 | feat: bcm-mock (CMDaemon JSON API subset) | partial | components/bcm-mock/bcm.py built; call names are an assumed subset (ADR 0005, tracker partial) |
| #61 | feat: SSH login node so SSH-based clients can reach the simulated cluster | shipped | images/login, ADR 0011 |
| #65 | decision: BCM surface for SSH-based clients (cmsh over SSH vs CMDaemon JSON) | shipped | ADR 0013 |
| #66 | feat: fake cmsh on the login node (BCM head node for SSH-based clients) | shipped | images/login/cmsh.py |
| #70 | feat: sshd on compute nodes (ssh <node> from the login node, ProxyJump, srun --pty) | shipped | images/node-base sshd, ADR 0014 |
| #71 | feat: SSH fault scenarios (login node down or slow, changed host key, session limit) | shipped | scenarios/ssh-*.yaml, login-node-*.yaml |
| #72 | feat: extra SSH-only endpoints for multi-cluster and jump-host tests | unverified | no endpoint for extra SSH-only multi-cluster or jump-host listeners found |
| #73 | docs: SSH connection-check recipe and scenario for client testing | shipped | docs/guides/ssh-connection-testing.md, scenarios/ssh-*.yaml |
| #74 | docs: user guide for testing SSH connections, with screenshots | shipped | docs/guides/img/ssh-0*.png, ssh-connection-testing.md |
| #75 | feat: ssh scenario steps for a refused key and a single node's sshd down | shipped | scenarios/ssh-*.yaml, login-node-*.yaml |
| #81 | feat: node and login shells behave like a normal shell (bash, tab completion, history, colours) | shipped | images/node-base/Dockerfile (bash, completion), tracker 'Normal shell on nodes' |
| #144 | feat: cmsh interactive console (read-only) on the login node | shipped | merged PR #134 (interactive read-only cmsh) |

## Scenarios and client testing

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #36 | feat: scenario engine (benchctl scenario list/run) | shipped | benchctl/scenario.py, scenarios/*.yaml |
| #39 | feat: Teleport access to the simulated cluster (app reaches nodes through Teleport) | not built | Teleport access: tracker todo, waits on open decisions |
| #68 | docs: guide for testing an HPC monitoring client against the workbench | shipped | docs/guides/client-testing.md |
| #69 | roadmap: cover the real-HPC surface a general monitoring client uses | partial | umbrella roadmap; several surfaces still open (see SPEC section 11) |
| #82 | docs: per-component setup guide for the monitoring client (SSH, Slurm, Grafana, Jira, BMC, BCM) | shipped | docs/guides/client-setup-by-component.md |
| #131 | test: check the monitoring client against the redesigned dashboard and apply-plan | unverified | manual run of the monitoring client against the redesigned dashboard; no recorded result in the repo |

## Slurm and workload

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #14 | Slurm images and generated slurm.conf (node-base, slurm-ctl) | shipped | benchctl/slurmconf.py, images/node-base, images/slurm-ctl; ADR 0002, 0003 |
| #17 | slurmrestd and accounting (slurmdbd + MariaDB on tmpfs) | shipped | benchctl/slurmconf.py, images/node-base, images/slurm-ctl; ADR 0002, 0003 |
| #83 | feat: gpu-exporter-farm follows Slurm allocations (busy GPUs for a job, idle otherwise) | shipped | merged PR #84 (images/slurm-ctl/alloc_feed.py) |
| #135 | fix: sacct says accounting storage is disabled in the default workbench | shipped | merged PR #134 (accounting default, sacct, power state) |
| #136 | fix: Power off from the page does not change sinfo (Slurm is never told) | shipped | merged PR #134 (accounting default, sacct, power state) |
| #138 | fix: a node set down from the page returns to service at once (ReturnToService=2) | shipped | merged PR #134 (accounting default, sacct, power state) |
| #139 | fix: benchctl up fails on accounting-init because the one-shot job exits 0 | shipped | merged PR #134 (accounting default, sacct, power state) |
| #140 | fix: a powered-off node keeps publishing GPU metrics | shipped | merged PR #134 (accounting default, sacct, power state) |
| #151 | fix: Power off must make slurmd stop answering so slurmctld detects the node down by itself | shipped | merged PR #150 (slurmd stops answering, scenario faults reach the cluster) |
| #152 | fix: scenario fault actions are not reflected on the cluster (power steps, and every Slurm step when played from the page) | shipped | merged PR #150 (slurmd stops answering, scenario faults reach the cluster) |
| #153 | feat: a minimal Slurm workload (user-sim) so queues, allocations and node faults show up in a job-centric client panel | shipped | merged PR #159 (per-node workload, benchctl submit) |
| #154 | Slurm marks a powered-off node down after about 60 s; make it about 30 s | shipped | benchctl/slurmconf.py: SlurmdTimeout=15 (about 30 s) |
| #155 | benchctl set node NAME on/off leaves the page showing the old power state | shipped | benchctl set node off/on runs the page's power step (benchctl/cli.py set_node) |
| #156 | Submit a workload on a chosen node (button on the node panel and benchctl submit) | shipped | merged PR #159 (per-node workload, benchctl submit) |

## Tests

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #55 | test: say why the real-test fixtures could not bring the stack up | shipped | tests/real/helpers.py bring_up, tests/test_bring_up.py |
| #133 | test: two unit tests fail when a workbench stack runs on the host | shipped | merged PR #134 |

## Web UI

| Issue | Title | Verdict | Evidence |
|---|---|---|---|
| #38 | feat(ui): Web UI base with Governor Dashboard and Scenario Orchestrator | shipped | webui/ (dashboard, scenario orchestrator); W-7 budget manager is a separate gap |
| #40 | feat(ui): UI/UX pass with a Playwright screenshot/check helper | shipped | scripts/ui_shots.py, tests/real/test_webui_browser.py |
| #42 | feat(ui): Cluster Canvas with live node state and fault actions | shipped | webui/cluster.py, webui/static/index.html (evolved into the entity list) |
| #46 | feat(ui): professional app shell and layout (sidebar, status header, overview-first hierarchy) | shipped | webui/cluster.py, webui/static/index.html (evolved into the entity list) |
| #49 | feat(ui): single-page cluster dashboard with governor left rail and operate right rail | shipped | webui/cluster.py, webui/static/index.html (evolved into the entity list) |
| #60 | feat(webui): improve the traffic animation design and add a Guide button that opens the manual | shipped | webui/flow.py traffic animation; webui/wiki.py help button |
| #79 | feat(ui): build scenarios visually with workflow diagrams (triggers and actions) | not built | visual scenario builder is not built |
| #115 | feat(ui): dashboard layout (rails that do not scroll the cluster away) and connection details for nodes and services | shipped | merged PR #116 to #130 (dashboard redesign series) |
| #117 | feat(ui): apply a new architecture from the Plan tab (governor apply-plan) | shipped | merged PR #119 (apply-plan, ADR 0017) |
| #118 | feat(ui): traffic view beside the nodes, with clients and nodes drawn in the diagram and a better animation | shipped | merged PR #116 to #130 (dashboard redesign series) |
| #121 | feat(ui): calmer, more breathable dashboard (less noise, more white space) | shipped | merged PR #116 to #130 (dashboard redesign series) |
| #123 | feat(ui): nodes as a compact highlight grid; give the traffic animation more room | shipped | merged PR #116 to #130 (dashboard redesign series) |
| #125 | feat(ui): governor panel from the top bar, entity tree, larger traffic diagram | shipped | merged PR #116 to #130 (dashboard redesign series) |
| #127 | feat(ui): traffic and tree side by side, whole dashboard in one screen | shipped | merged PR #116 to #130 (dashboard redesign series) |
| #129 | feat(ui): minimalist entity list: one card per line with status and quick facts | shipped | merged PR #116 to #130 (dashboard redesign series) |
| #137 | feat(ui): one Settings sheet for options that change memory (accounting first), driven by a registry | shipped | merged PR #134 (Cluster architecture sheet, PART_INFO registry) |
| #142 | feat(ui): the icons in the traffic diagram are clickable | shipped | merged PR #134 (Cluster architecture sheet, PART_INFO registry) |
| #172 | Web UI: declutter the top bar (status only) and move actions into one grouped menu | shipped | merged PR #173 (top bar menu) |

## Still open after triage

Everything marked partial, not built or unverified above. Pick from there; each needs its own branch and issue work as in CLAUDE.md.
