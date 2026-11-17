# ADR 0084: Kubernetes through Teleport and for the monitoring client: what to build, in what order

Status: decided on the owner's delegation (2026-11-15), revised the same day after the owner's correction (the monitoring client has a Kubernetes feature); owner review open; group 4 decisions added 2026-11-17 (issue 867, addendum at the end). Decision issue 751; client-driven work items 762 to 769, 382, 390, 760; `tsh kube` fidelity items 752 to 759. Builds on [ADR 0083](0083-kubernetes-through-teleport.md) (issue 383), [ADR 0034](0034-kubernetes-kwok-first.md) and [ADR 0020](0020-teleport-agentless-openssh.md); epic 471.

## Context
ADR 0083 made `tsh kube` work: Teleport 18.11.8 Community Edition, auth, proxy and a `kubernetes_service` in one process on port 3080, one role (`workbench-user`, role v7) for every user bound to a read-only ClusterRole; five live tests passed. The owner wants the simulation "real, like working on a production cluster".

**Correction (owner, 2026-11-15, supersedes an earlier message).** The monitoring client has a Kubernetes feature and is in active development. I read its own Kubernetes guide, its command code and its type definitions (read-only; not named or linked here) and verified them against the simulation's code.

The decisive fact: **the client does not use `tsh kube`.** It runs fixed read-only `kubectl` scripts over the SSH session the user already has open (direct SSH, jump host, Azure, or `tsh ssh` through Teleport) on a "Kubernetes host" (a control-plane or admin node, or the node it is connected to), using a kubeconfig on that host.

So the Teleport path that matters is SSH through the proxy to the k-login, then `kubectl` there. The first version of this ADR ranked `tsh kube` fidelity first and assumed no client; that ranking is withdrawn. The decision is now driven by what the client does, then by production fidelity, cost in memory (16 GB workstation; Teleport cap 320 MB, measured 222 MB before the Kubernetes Service), complexity and risk.

Live checks for this ADR were **not run**: an owner-side `verify.sh --full --with-jira` was active (checked again with `pgrep` before this revision). Every fact below is from public documents, the client's files, ADR 0083's live results or local read-only `tsh` commands; each says which.

## What the client does today

Each point is behaviour read from the client's guide and code, mapped to what the simulation serves today (checked in `k8s/fixtures`, `benchctl/k8s.py`, `images/login/login.sh`, `tests/real/test_k8s_real.py`) and the gap.

| Client behaviour | Simulation today | Gap and item |
|---|---|---|
| Runs over the existing SSH session on the connected node or on a "Kubernetes host" reached by `ssh <host>` from it; one fixed bash script per refresh (one round trip), values passed as quoted parameters | The k-login has `kubectl` and kubeconfigs; direct SSH and Teleport reach it | The `ssh <host>` hop is untested: 762 |
| Kubeconfig: an absolute path on that host, or `KUBECONFIG` or `~/.kube/config`; token-based; refuses a file owned by another user (root accepted) or writable by others (group write only for the user's own private group) because an exec credential plugin would run on every poll; a kubeconfig that signs in through a browser cannot work. Discover looks for `kubectl`, `k3s`, `microk8s`, `oc`, and for kubeconfigs in `~/.kube`, `KUBECONFIG` and files in `~` and `~/Downloads` | `~/.kube/config` and `limited.yaml`, mode 600, owned by the user (live-tested, `test_the_login_node_kubeconfig_is_the_users_own_mode_600_and_read_only`) | No negative or exec-plugin file: 767 |
| `kubectl auth can-i list nodes`, pods and events cluster-wide, per namespace and in the context namespace (cached 10 minutes); `config view --minify` | Real API server; viewer and limited user exist | Only partly asserted: 762 |
| `get --raw /readyz?verbose` and `/version` | Real API server | None |
| Nodes: conditions, kubelet version and skew, taints, unschedulable, `nvidia.com/gpu` capacity and allocatable, GPU Feature Discovery labels (product, count, MIG strategy, driver, present), a pool label; "GPUs not advertised" is inferred from labels plus allocatable 0 | Labels with `mig.strategy: none`, 8 GPUs per node, `pool` label, one kubelet version | MIG, unadvertised node, kubelet skew: 766 |
| Node usage from the metrics API: probe `/apis/metrics.k8s.io/v1beta1/nodes?limit=1`, then `nodes.metrics.k8s.io` | 404 (no metrics-server) | Now has a real consumer: 382 |
| Pods: restarts, waiting reasons, OOM, `PodScheduled` reason, CPU and memory requests; `--chunk-size=500`; a pod-count guard that skips pod lists above 10,000 pods; Warning events (newest 100), evictions | KWOK stages give crash loop, OOM, image pull, pending; real scheduler events; `evict` step | Scale and the 10,000 guard: 768 |
| Workload signals: Deployments, Jobs, PVCs, DaemonSets, ResourceQuotas (90% or more), HPAs at maximum, Kueue ClusterQueues and LocalQueues, Kubeflow PyTorchJobs and MPIJobs, Volcano Queues and PodGroups; each only where the user may list it and the CRD is installed (`api-resources --api-group`) | The first five and Kueue exist (`20-signals.yaml`, `k8s/kueue/`); **no HPA object, no Kubeflow, no Volcano** | 764 (HPA), 390 (Kubeflow), 765 (Volcano) |
| Describe and Logs buttons (`kubectl describe pod`; `kubectl logs --tail=500 --timestamps --limit-bytes ... [--previous]`), on click only | Describe works; logs fail (no kubelet) | 760 (logs first) |
| Browse: all pods of a namespace (up to 2,000), namespace picker; Security: `auth can-i --list` and a pod security posture (privileged, host namespaces, hostPath, capabilities, root) | Works; one privileged pod fixture | Asserted in 762 |
| Limited user: no node or cluster-wide pod rights gives a "limited view" with a note, not an error; no `pods/log` right gives a note | `limited.kubeconfig` (team-a, pods and events only) | Asserted in 762 |
| Alerts: node NotReady and back, new OOM or crash loop, evictions; never polls a Teleport cluster in the background (each run is an audited session) | Scenarios `k8s-node-notready`, `pod-crashloop` and steps exist | Owner-run (381) |
| Kubernetes versions: tested on 1.29 and a recent k3s; older untested; thousands of nodes untested; MIG, several GPUs per node, time-slicing untested | Server and kubectl v1.36.1 by default; 4 nodes by default | 769, 768, 766 |
| Teleport: a real `tsh login` profile (the client reads `tsh status --format=json`, wants more than 300 s left), then `tsh ssh` without a PTY (the client's own wrapper refuses to log in by itself; `--no-login` is the wrapper's option, not a `tsh` flag, see the group 4 addendum) with one `bash` kept open and fed commands; a "light mode" of one session per cycle when that is refused (MFA, access denied, session limit); expired profile gives "waiting for a Teleport login" | Identity files only (they do not create a profile); no kubectl test through `tsh ssh` | 763 |

## Decision

### Work items, ranked

Group A is what the client exercises. Group B is `tsh kube` production fidelity, which the client does not use; it comes after A.

| Rank | Item | Decision | Issue |
|---|---|---|---|
| A1 | Conformance test of every read the client makes, as viewer and as limited user, from the k-login, including the `ssh <host>` hop | Accept, first | 762 (M) |
| A2 | Teleport audit helper (read Teleport's audit log file) | Accept, before A3 | 754 (S) |
| A3 | The client's Teleport path end to end: `tsh login` profile, `tsh ssh --no-login` without a PTY, kubectl on the k-login, profile expiry, audit events | Accept | 763 (M, spike first) |
| A4 | metrics-server so the metrics API answers (now has a consumer) | Accept, promoted | 382 (M) |
| A5 | HPA fixtures | Accept | 764 (S) |
| A6 | Kubeconfig fixtures: loose, foreign-owned, exec plugin, browser sign-in | Accept | 767 (S) |
| A7 | `kubectl logs` for KWOK pods (client Logs button), then exec | Accept, re-scoped | 760 (S spike, then build) |
| A8 | Kubeflow PyTorchJob and MPIJob definitions and fixtures | Accept, re-scoped to fixtures first | 390 (M) |
| A9 | Volcano Queue and PodGroup definitions and fixtures | Accept | 765 (M) |
| A10 | GPU shapes: MIG, GPUs not advertised, fragmentation, kubelet skew | Accept | 766 (M) |
| A11 | Scale: hundreds to thousands of KWOK nodes, 10,000 pods | Accept, measure first | 768 (M) |
| A12 | Kubernetes 1.29 and current | Spike | 769 (S) |
| A13 | Measured Teleport memory (the proxy to the k-login) | Accept | 752 (S) |
| B1 | Role tiers viewer / team-a developer / admin with role v8 | **Demoted** to after A | 753 (M) |
| B2 | Failure scenarios that exist only on the `tsh kube` path | **Demoted** (API certificate and Teleport outage or expiry move to A1 and A3) | 756 (M) |
| B3 | Page view of kube counters and events | **Demoted** | 755 (M) |
| B4 | Machine ID (`tbot`) automation identity | **Demoted** | 759 (M) |
| B5 | Separate Kubernetes agent container | **Deferred, effectively dropped** while no client uses `tsh kube` | 757 (L, gate kept) |
| B6 | Access Requests | **Deferred, effectively dropped** | 758 (S) |
| B7 | Per-session MFA, Enterprise requests, session sharing, proxy certificate change fault, a second cluster, a pin change | Rejected or deferred as before (see "Rejected") | none |

What I demote or drop, and why: 753, 755, 756 and 759 are real production behaviour but the client reads none of it, so they wait for the client work. 757 and 758 are deferred because nothing in the client needs a separate agent or an Access Request and the first carries unmeasured memory risk. None is deleted: the owner can pull any back. 754 and 752 stay early because Teleport is the client's proxy to the k-login and their helpers are reused.

### Order of work
1. 762 (acceptance: every read in the table has an assertion for viewer and limited user).
2. 754, then 763 (acceptance: the client's Teleport path runs as a script and expiry and audit events are asserted; spike result stated).
3. 382, 764, 767 (acceptance: probe answers 200; HPA states asserted; each kubeconfig file has the stated owner and mode).
4. 760 (acceptance: `kubectl logs --previous` answers for the crash-loop pod, or the spike says why not).
5. 390, 765 (acceptance: `api-resources` lists the groups; one failed and one running object each).
6. 766, 768, 769 (acceptance: shapes asserted; memory table for N nodes; version result).
7. The owner's client pass (381) after each batch, using the list below.
8. Group B in the order 752, 753, 756, 755, 759.

### Tool scope

A tool a client calls is tested end to end even if the simulation never needed it before (owner rule). Legend: tested = a live test exists; planned = an issue gives it one.

| Tool | Status | Why |
|---|---|---|
| `kubectl` read-only calls over SSH on the k-login (the client's reads) | supported, tested in part; full in 762 | The client's actual Kubernetes path |
| `ssh <host>` from the connected node to a Kubernetes host | planned (762) | The client's "Kubernetes host" option |
| `tsh login` (profile), `tsh status --format=json`, `tsh --proxy=... [--user=...] [--insecure] ssh` without a PTY | planned (763) | The client's Teleport path; identity files cannot stand in for a profile |
| Discover's `command -v kubectl k3s microk8s oc` and kubeconfig scan | planned (767, owner-run) | Needs the negative files |
| `tsh kube ls`, `login`, `credentials`, `kubectl` through the proxy | supported / tested (ADR 0083) | Production fidelity; no client use today |
| `tsh proxy kube`, `tsh kubectl`, `tsh kube exec`, `tsh sessions` | untested, not planned unless 753 or 760 are built | The client does not call them |
| `tsh kube join` | out of scope | Session sharing is Enterprise (feature matrix) |
| `tctl` | supported inside the container | Admin tool |
| `tbot` | not planned (759 demoted) | Client does not use Machine ID |
| Lens and other desktop tools reading a kubeconfig, Teleport Connect, Teleport web UI | untested by design | Not client paths |

## The Teleport path the client really uses, and what `tsh kube` adds
- **Path:** the user logs in with `tsh login` (local password and one-time code, or an SSO connector), the client checks the profile (and never logs in by itself), runs `tsh ssh user@k-login` with `bash` and feeds fixed scripts, and `kubectl` on the k-login reads the cluster with the user's own kubeconfig. Teleport sees SSH sessions, not Kubernetes requests. (Client's guide and code; the "five audit events per `tsh ssh`" in its comments is the client authors' note, not verified here.)
- **What the simulation must prove (763):** a k-login in Teleport; the SSH session without a PTY; the same answers as direct SSH; profile expiry; the audit events; the refusal cases the client reacts to.
- **What `tsh kube` adds for the client today: nothing.** Its kubectl runs on the node, not on the desktop, with the node's kubeconfig. `tsh kube login` writes an exec-plugin kubeconfig, which needs a live `tsh` session where it runs, so it does not fit a node the client reads. A future client option (kubectl on the desktop through Teleport) would use it; the ADR 0083 support stays as a tested reference for that.

## Client-facing test plan

**Without the client (simulation side).** Each read the client makes is run as a plain `kubectl` command from the k-login as `alice` and as the limited user, and its answer shape asserted (762), then through Teleport (763). The commands are written in this repository's own words in `docs/guides/client-testing.md` (decision: not copied from the client; say if you want them verbatim). Fixtures and scenarios from A4 to A11 make each client screen have something to show.

**What to ask the owner to run in the client**, after each batch:
1. Discover on the k-login: finds `config` and `limited.yaml`; judges the loose and foreign-owned files as refused and the exec-plugin file as it should (767).
2. Nodes and GPUs tabs: grouping by the `pool` label and by GPU product, CPU and memory use (382), a MIG node, a node with GPUs not advertised, GPU fragmentation, a kubelet skew flag (766).
3. Attention tab: pods that need attention, Warning events, evictions (scenarios); workload signals on: stalled Deployment, failed Job, Pending PVC, DaemonSet not ready, quota at 90%, HPA at maximum (764), Kueue, Kubeflow (390), Volcano (765).
4. Limited user: the limited view and the logs note.
5. Describe and Logs on a pod, including `--previous` (760).
6. Security: what this user may do, pod security posture.
7. Notifications: node NotReady and back, new OOM, evictions.
8. A Teleport cluster: refresh by hand, expired profile, "waiting for a Teleport login" (763). Direct SSH and jump host for comparison.
9. Scale and old versions: the largest safe node count (768); 1.29 (769).
State what was and was not exercised in each PR (definition of done).

## If the client ever used `tsh kube`
Ranking is inference, not a requirement. The likeliest option is the client calling `tsh kube login` and a local `kubectl` or client-go with the exec plugin `tsh kube credentials` (the generated kubeconfig; Teleport's FAQ names Lens and says Connect sets `KUBECONFIG` per cluster); next `tsh proxy kube` (a local listener); then an identity file or `tbot` for headless use. Choices that would need making: exec plugin or local proxy; where the kubeconfig lives and how it passes the client's own owner and mode check; who holds the one-time code; certificate expiry mid-session; namespace-limited users. None is decided here; items 753, 759 and 756 would be built then.

## The pin and the Kubernetes RBAC advisory

- **What:** "[High] Kubernetes Access URL-encoding RBAC bypass": Teleport could misclassify URL-encoded Kubernetes subresource requests, so exec, attach or port-forward could bypass Teleport RBAC in certain proxying paths. Advised: upgrade proxy and Kubernetes services.
- **Fixed in:** private security release 18.10.1 (2026-07-17); announced in the changelog entry of 18.10.7 (2026-08-19), "a public equivalent of 18.10.6 that announces security issues addressed in the private release 18.10.1". Source: https://goteleport.com/docs/changelog/ . The changelog text shows no CVE or advisory identifier.
- **Our pin:** 18.11.8 (2026-10-29). 18.11.0 was released 2026-08-25, after both the fix and its announcement, so on release order **18.11.8 contains the fix and is not affected**. This is an inference from dates; the changelog does not say in words that the 18.11 line carries it, and it was not tested.
- **Minimum safe:** 18.10.1 on the 18.10 branch; 18.11.0 on the 18.11 line (same inference). Private security releases 18.11.3 and 18.11.5 are not described in the changelog yet; 18.11.8 is later than both, so stay at 18.11.5 or later.
- **Decision:** no pin change and no work item. The latest on the line is 18.11.10 (2026-11-10), with tsh kube fixes in 18.11.9 and no security text. A bump touches `sim.yaml` `teleport.version`, `DEFAULT_VERSION` in `benchctl/teleport.py`, the 18.11.8 strings in `tests/test_teleport.py`, `tests/fixtures/lab/*/versions.yaml`, `tests/test_lab_import.py` and `docs/guides/config-lab.md`, and **starts a new Teleport cluster** because `restore` needs the exact version string (every user is invited again). Do it only when a fix is needed.

## Evidence

Read on 2026-11-15; `.md` renderings of the Teleport docs pages were fetched. "Vendor" marks marketing or a case study written by Teleport.

1. **Production runs the Kubernetes Service as an agent that dials the proxy.** Agents "establish an SSH reverse tunnel to the Proxy Service"; direct mode "is designed for legacy use cases and only supports self-hosted Teleport clusters". https://goteleport.com/docs/reference/architecture/agents/ . The fastest registration is a Kubernetes Service instance on the cluster (Helm chart `teleport-kube-agent`); the other documented way is a Linux host outside the cluster with a static kubeconfig, "useful if you want to decouple your Teleport deployment from the Kubernetes clusters". https://goteleport.com/docs/enroll-resources/kubernetes-access/introduction/ and .../register-clusters/static-kubeconfig/ . The chart's sample sizing is a 2 GiB request and limit https://goteleport.com/docs/reference/helm-reference/teleport-kube-agent/ (production sizing, not a measured use).
2. **Our current shape is the direct listener.** Teleport's error "set kubernetes_service.listen_addr if this process can be reached from a teleport proxy, or point teleport.proxy_server to a proxy to dial out" (ADR 0083, verified live). A pod service account is impossible here: KWOK pods run no containers (ADR 0034).
3. **Roles.** `kubernetes_groups`/`kubernetes_users` are impersonated; "if they do not exist in the cluster, Kubernetes RBAC will ignore them"; deny rules remove principals; `kubernetes_resources` v8 uses plural kinds, `api_group`, `namespace`, `name`, `verbs` (get, list, create, update, patch, delete, deletecollection, watch, portforward, exec); a namespace limit in Teleport must be repeated in Kubernetes RBAC "to avoid leaking resources". https://goteleport.com/docs/enroll-resources/kubernetes-access/controls/ . Role versions: v8 added CRD support and is not compatible with v17 agents; default `kubernetes_resources` for v8 is the wildcard for namespaced and cluster-wide resources; v7's is the wildcard kind. https://goteleport.com/docs/reference/access-controls/roles/ . The 18.0.0 release notes introduce v8 (`api_group` required, `deployment` becomes `deployments`) https://goteleport.com/docs/changelog/ . 18.9.0 added a `proxy` verb for API-server proxy subresources; 18.10.0 made ephemeral containers need `exec` plus `patch`/`update`. Same changelog.
4. **Audit events** (docs examples, not seen from our setup): `kube.request` (T3009I) with `user`, `kubernetes_cluster`, `verb`, `request_path`, `resource_api_group`, `resource_kind`, `resource_name`, `resource_namespace`, `response_code`, `login`, `addr.*`, `server_id`; `kube.create`/`kube.update`/`kube.delete` (T3010I, T3011I, T3012I) for cluster resources. https://goteleport.com/docs/reference/audit-events/kubernetes-access/ . `exec` (T3002I, with `proto: kube`, `kubernetes_cluster`, `sid`), `session.start`, `session.end`, `session.join`: https://goteleport.com/docs/reference/audit-events/ssh-sessions/ . The `dir` backend writes one JSON line per event, one file per day, under the data directory's `log/<service uuid>/`. https://goteleport.com/docs/reference/deployment/monitoring/audit/ . Metrics `teleport_kubernetes_server_api_requests_total`, `..._exec_sessions_total`, `..._portforward_sessions_total` https://goteleport.com/docs/reference/deployment/monitoring/metrics/ . `tctl get events` does not appear in the `tctl` reference I read; `tctl audit query` and `tctl investigate` belong to Identity Security (not shown to be in Community).
5. **Community Edition** (feature matrix https://goteleport.com/docs/feature-matrix/): Kubernetes access yes; "Just-in-Time Access Requests: only can request roles through CLI"; automatic requests no; Access Request plugin integrations (Slack, Teams) yes; per-session MFA yes; session recording yes; session sharing and moderation no; dual authorization no; Machine ID issuance, secretless authentication, auditability yes (HSM/TPM, external PKI no). Community role requests are approved with `tctl requests approve/deny` on the Auth Service: https://goteleport.com/docs/identity-governance/access-requests/oss-role-requests/ . Resource Access Requests ("Identity Governance is available only with Teleport Enterprise"; `kube_cluster`, `kube_resource` kinds): https://goteleport.com/docs/identity-governance/access-requests/resource-requests/ .
6. **Machine ID for Kubernetes:** the bot's role carries `kubernetes_groups`, bound in Kubernetes; `tbot` runs a `kubernetes/v2` output that writes `kubeconfig.yaml` ("compatible with most Kubernetes tools"). https://goteleport.com/docs/machine-workload-identity/access-guides/kubernetes/ . Non-interactive callers use `TELEPORT_IDENTITY_FILE` and `--proxy` (`tctl` also `--auth-server`), and Machine ID otherwise (static-kubeconfig and per-session-MFA pages above).
7. **Per-session MFA:** satisfied by WebAuthn or an IdP, "cannot be satisfied by an OTP device"; "only `kubectl` supports per-session WebAuthn authentication for Kubernetes"; `tsh proxy kube` does one check for many clusters from 18.12.0. https://goteleport.com/docs/zero-trust-access/authentication/per-session-mfa/ . The FAQ states `tsh kube login` writes a kubeconfig and "GUI tools such as Lens can interact with the Kubernetes cluster through Teleport"; one `kubernetes_service` can serve several clusters. https://goteleport.com/docs/enroll-resources/kubernetes-access/faq/ . Teleport Connect "runs `tshd`, a `tsh` daemon that manages user certificates and kubeconfigs", sets `KUBECONFIG` per cluster. https://goteleport.com/docs/reference/architecture/agents/ and .../teleport-clients/teleport-connect/ .
8. **`tsh` reference** (https://goteleport.com/docs/reference/cli/tsh/): `tsh kube exec`, `join` (moderated sessions), `login` (`--as`, `--as-groups`, `-n`), `ls`, `sessions` (deprecated for `tsh sessions ls --kind=kube`), `tsh kubectl`, `tsh proxy kube` (`--exec`, `--all`, `--labels`). `tsh kube credentials` is not in it; verified live (local, read-only): `tsh kube credentials --help` on tsh 18.11.2 exists and takes `-i`/`--identity` and `TELEPORT_IDENTITY_FILE`; `tbot version` on the host prints 18.11.2.
9. **Changelog notes** (https://goteleport.com/docs/changelog/): 18.11.9 "Fixed `tsh proxy kube` failing for every cluster when one Kubernetes cluster has no running agent" and multiplexing over HTTP/2; 18.11.10 is the latest on the line.
10. **How AI and GPU teams actually do it, what exists in public:**
    - Together AI documents Kubernetes access to customer GPU clusters with OIDC, `kubelogin`, one-hour tokens and plain RBAC bindings per user (no Teleport). https://docs.together.ai/docs/cluster-oidc
    - Voltage Park (GPU cloud) argues for OIDC and short-lived tokens instead of static kubeconfigs; marketing, no setup detail. https://www.voltagepark.com/blog/how-to-use-oidc-for-secure-streamlined-kubernetes-access
    - A computer-science research lab running AI and data-science infrastructure (three administrators, 40 servers, 200 users) describes Teleport's free tier with GitHub login, OTP, audit logging, Machine ID for GitLab CI and a pull-request-based access flow; it does not mention `tsh kube` or Teleport roles for Kubernetes. https://cloudchirps.substack.com/p/how-we-manage-on-prem-infrastructure (a newsletter post, 2025-02-10)
    - NVIDIA describes a central identity gateway with OIDC and session cookies across Kubernetes-based AI platforms; Teleport is not mentioned. https://developer.nvidia.com/blog/how-to-carry-user-identity-across-federated-kubernetes-and-ai-platforms/
    - Teleport's AI-infrastructure page claims short-lived identity, just-in-time access and session recording for GPU clusters: **vendor marketing**, no architecture or numbers, named customers are not tied to GPU work. https://goteleport.com/platform/ai-infrastructure/
    - **Nothing public found** of a named GPU or AI operator describing `tsh kube` in production.
11. **Kubernetes customers (vendor case studies):** Exness: hundreds of clusters, Machine ID "replaced permanent Kubernetes tokens used in GitLab pipelines", Slack access requests with double approval for the most sensitive, two agent replicas per cluster, Helm and Terraform. https://goteleport.com/case-study/exness/ . thredUP: Teleport maps SSO attributes to roles and then to Kubernetes subjects; logs `kubectl` events and records sessions; the page does not mention `tsh kube`. https://goteleport.com/case-study/thredup/ . Correction to an earlier note: **Turo** deploys Teleport inside EKS and lists Kubernetes clusters as a planned expansion; its short-lived-certificate claim is about databases. https://goteleport.com/case-study/turo/ . ExtraHop and Rush Street were not read (Rush Street has one marketing quote about closing the Kubernetes API, on the AI-infrastructure page above).
12. **KWOK** has `Exec`/`ClusterExec`, `PortForward` and `Logs`/`ClusterLogs` resources whose commands run in KWOK's own container (search result of the KWOK docs https://kwok.sigs.k8s.io/docs/user/exec-configuration , `.../port-forward-configuration`, `.../logs-configuration`; the pages were not fetched). The cluster enables only `--enable-crds=Stage` (ADR 0034).
13. **Repo facts** (read): one role for every user; `restore` needs the exact Teleport version string; the audit and data directory is the host-mounted `compose/generated/teleport/data`; Teleport budget 222 MB measured, 256 expected, 320 cap (`docs/architecture/04-memory-and-profiles.md`).
14. **The monitoring client** (its own Kubernetes guide, command code and type definitions, read read-only on 2026-11-15; not named or linked): runs `kubectl` read-only scripts over its SSH session on a Kubernetes host with a kubeconfig on that host; refuses a kubeconfig owned by another user or writable by others because an exec plugin runs on every poll; reads `auth can-i`, `config view --minify`, `/readyz?verbose`, `/version`, nodes, the metrics API, pods, Warning events, and with workload signals Deployments, Jobs, PVCs, DaemonSets, ResourceQuotas, HPAs, Kueue, Kubeflow and Volcano objects; describe and logs on click; never runs `exec`, `port-forward` or `proxy`; for Teleport uses `tsh status --format=json`, then `tsh ssh` (its wrapper's no-login option, not a `tsh` flag), and never polls a Teleport cluster by itself. Its own "not verified yet" list: Teleport, Azure and jump-host paths against a live cluster, workload signals against live Kueue, Kubeflow and Volcano, MIG, several GPUs per node and time-slicing, thousands of nodes, Kubernetes older than 1.29. The command text was not copied into this repository.
15. **The simulation's current state** (read in this repository): HPA is only readable by the viewer role, no HPA object exists; Kueue CRDs and objects exist (ADR 0034 addendum); no Kubeflow or Volcano CRD; no metrics-server (issue 382); `mig.strategy` is `none` on every node; the k-login kubeconfigs are mode 600 and user-owned; no live test runs kubectl through `tsh ssh`; issue 381 records that the client's importer and its kubectl commands were run as `alice` on the k-login but not its screens.

## Rejected
- **Ranking `tsh kube` fidelity ahead of the client's reads** (the first version of this ADR). The client does not call `tsh kube`.
- **Copying the client's scripts into the repository's tests.** The tests use the same kubectl reads in the repository's own words; the client's command text stays out (owner rule: no client names or paths; and the simulation must not be fitted to one client's quirks, `docs/guides/client-testing.md`).
- **Per-session MFA.** OTP cannot satisfy it; the client falls back to a session-per-cycle mode on refusal and cannot prompt.
- **Resource Access Requests, reviewer rules, session sharing, dual authorization.** Not in Community Edition (feature matrix).
- **Proxy certificate change fault.** The client path uses `--insecure` for the lab proxy.
- **A second Kubernetes cluster now.** Memory; epic 256.
- **Bumping the pin now.** Not affected by the advisory; the bump starts a new cluster.

## Consequences
- The next work is the client's reads, not Teleport's Kubernetes service. ADR 0083's `tsh kube` support stays as built and tested; its extensions (753, 756, 755, 759) wait.
- `docs/guides/client-testing.md` gains the read list, the negative kubeconfig files and the Teleport path as each item lands; the progress tracker changes with each item, not with this ADR.
- Memory: only 382 (metrics-server) and 768 (scale) change what the Kubernetes part costs; both measure first and set the cap in their own PR.
- A client pass by the owner follows each batch; what was and was not exercised goes into each PR.

## Not verified
Marked with the issue that will verify it.
- Everything about the client comes from its documentation and code read once; no client screen was run against the simulation (381; owner-run).
- That the pinned 18.11.8 accepts v8 roles (753, demoted); the audit fields and file location 18.11.8 writes (754); the number of audit events per `tsh ssh` (763).
- A scripted `tsh login` profile for a sim user (763 spike).
- Memory of the Kubernetes Service in the Teleport container (752); of a separate agent (757, deferred).
- `tsh proxy kube`, `tsh kubectl`, `tsh kube exec`, `tsh sessions` with an identity file; `tbot` on Community; `tsh request` with an identity file; KWOK Exec and Logs through Teleport (the KWOK pages were not fetched, only a search listing; 760).
- Whether KWOK can serve `kubectl logs --previous` for a crash-looping pod (760).
- That 18.11.8 contains the Kubernetes RBAC fix: inferred from release dates, not stated by Teleport and not tested.
- Time-slicing: which signals the client reads is not in the client files I read (question below).
- ExtraHop's and Rush Street's case studies; any GPU or AI operator using `tsh kube` (none found); the default kubeconfig context name written by `tsh kube login`; 2026 Community licence thresholds (ADR 0020 addendum, issue 249).

## Questions for the owner
1. Time-slicing: the client's guide lists it as untested but I could not see what the client reads for it (labels, resources). What must the simulation serve?
2. Teleport path test (763): is a scripted `tsh login` profile required (a spike; Teleport documents no scripted password setup), or is a wire test with an identity file plus your own manual run of the profile path enough?
3. Scale (768): what node count is the target for "thousands of nodes" (1,000, 2,000, 5,000)? The 16 GB host may cap it; the measurement will say where.
4. The read list in `client-testing.md` will be in my own words, not the client's text; say if you want it verbatim.
5. The `tsh kube` items 753 to 759 are demoted or deferred. Say which, if any, you want pulled forward.

## Addendum: group 4 decisions (issue 867, 2026-11-17)

Decision phase for 752 to 760 and 762 to 769, with 382, 384, 385, 386 and 864 read for overlap. Nothing was built and no live test was run. Every "supports" below comes from the cited upstream source at the stated version, or from a read-only local `--help` of `tsh` and `tctl` 18.11.2. Client facts were read once more from its source, read-only, and are described in this repository's own words, with no client name, file or identifier.

### Corrections to the text above and to the issues
These change a planned test or fixture. Edit each issue to match before it is built.
1. **`tsh ssh --no-login` does not exist.** `--no-login` is an option of the client's own `tsh` wrapper. The wrapper reads `tsh status --format=json`. It fails instead of logging in when no profile has enough time left, then runs `tsh --proxy=P [--user=U] [--insecure] ssh [--cluster=C] ...`. `tsh ssh --help` (18.11.2) lists `--[no-]relogin` and `-t/--[no-]tty`, and no `--no-login`. In 763 the command becomes `tsh status --format=json`, then `tsh --proxy=... ssh alice@k-login -- bash -s` with stdin piped (no PTY). The text above is corrected in place.
2. **KWOK nodes advertise `--node-ip=127.0.0.1`** (`benchctl/k8s.py`). The API server proxies `kubectl logs` to the node's status address and kubelet port. metrics-server with `--kubelet-use-node-status-port` dials the same address. From either container, 127.0.0.1 is the container itself. Prerequisite **P1**, for 760 and 382:
   - `--node-ip` set to the kwok-controller's fixed address (kwokctl passes it explicitly, `pkg/kwokctl/components/kwok_controller.go` v0.8.0)
   - `--enable-crds` extended to `Logs`, `ClusterLogs`, `Metric` and `ClusterResourceUsage`
   - canned log files mounted read-only into kwok-controller
   - the API server's kubelet client flags, if KWOK asks for a client certificate (unverified)
3. **764: a patched HPA status does not survive.** The real HPA controller runs in kube-controller-manager and rewrites the conditions on every sync (default 15 s). Without a metrics API it sets `ScalingActive=False`. The Kueue pattern (no controller) does not transfer. 764 depends on 382 (see the table).
4. **765: Volcano `PodGroup` v1beta1 has no status subresource** (CRD at v1.16.0: `subresources: []`). Its status is written by a plain apply. `Queue` has a status subresource.
5. **766: every KWOK node reports `kubeletVersion: kwok-v0.8.0`.** The client reads the major and minor version from a `vX.Y` prefix. This string has none, so the client judges no node's skew today. Default nodes must report a real version, the API server's. The skew fixtures then add one node that is newer and one more than three minors behind (two minors below 1.25, per the skew policy).
6. **384 is half built:** `k8s.api_requests()` already feeds `webui/flow.py` a total of all callers. Re-scope 384 to per-user attribution (see the table) or close it.

### Decision table
Memory is the change to the Kubernetes or Teleport part; "none" means no new process. Model is the suggested implementer: Opus for spikes and open design, Sonnet for well-specified fixtures and tests. Every acceptance test is live and owner-run.

| Issue | What the tool supports (source) | What it does not | Decision | Memory | Depends on | Risk | Acceptance test | Model |
|---|---|---|---|---|---|---|---|---|
| 762 conformance | Every read is plain API: RBAC, `auth can-i`, `--chunk-size`, `remainingItemCount` on a `limit=1` list | The `ssh <host>` hop needs the user's key on the first node (shared home, ADR 0078); unverified | Accept, first. Include the real `kubeletVersion` default (correction 5) so skew is judged | none | none | Low. The hop may need a key-distribution fix | Each read asserted for viewer and limited user. Metrics probe 404 recorded. Volcano and Kubeflow asserted as "not served". `ssh -o BatchMode=yes k-login kubectl version` from the s-login | Sonnet |
| 754 audit helper | `dir` backend: one JSON line per event under `data_dir/log/<uuid>/`; `kube.request` T3009I fields (Teleport audit and kubernetes-access event references) | The exact fields 18.11.8 writes were not seen | Accept as written | none | none | Low | A `kube.request` line with user, cluster, verb, and response 200, then 403 | Sonnet |
| 763 client Teleport path | `tsh status --format=json`; `tsh ssh` without `-t`. OTP cannot satisfy per-session MFA (Teleport per-session MFA page). `max_connections` is Enterprise-only; `max_sessions` limits channels per connection (roles reference, 18.x) | No scripted local login: `tctl auth sign --format` writes identity files (file, openssh, tls, kubernetes, ...), not a profile. Headless login needs WebAuthn | Accept with correction 1. The default is a wire test with an identity file. The profile path is owner-run unless Q2 says otherwise. Refusal cases: access denied (a role without the login) and MFA (`require_session_mfa: true`). A session-limit refusal cannot be made on Community Edition | none | 754 | Medium. Automating the profile would rest on the web UI's internal invite API | Same answers as direct SSH, no PTY. An expired identity gives a non-zero exit, not a hang. Audit events per session counted. The two refusal messages asserted | Opus |
| 382 metrics-server | KWOK `Metric` path `/metrics/nodes/{nodeName}/metrics/resource` with the node annotation `metrics.k8s.io/resource-metrics-path`; metrics-server 0.7.0 or later. Usage comes from the pod annotations `kwok.x-k8s.io/usage-cpu` and `kwok.x-k8s.io/usage-memory` via `ClusterResourceUsage` (KWOK v0.8.0 resource-usage and metrics docs). kwokctl runs metrics-server 0.8.1 with `--kubelet-insecure-tls --kubelet-use-node-status-port` | No kube-proxy, so the `v1beta1.metrics.k8s.io` APIService must reach metrics-server's fixed address directly (a Service without a selector plus an EndpointSlice); to verify | Accept, with P1. Pin metrics-server v0.9.1 (2026-10-29, latest) or 0.8.1 (what kwokctl v0.8.0 tests). One unprivileged container, P3, at a fixed address | +1 container. The upstream manifest requests 200 MiB, which is a request, not a measurement. Measure, then set the cap | P1 | Medium (aggregation wiring) | Probe answers 200. `kubectl top nodes` lists every KWOK node. A stop step gives 503 and the client's "no usage" state; restart restores it | Opus |
| 764 HPA | Real controller (`horizontal.go`, v1.36.1): `TooManyReplicas` gives `ScalingLimited=True`; desired equal to max gives `DesiredWithinRange` and `ScalingLimited=False`. The scale-up limit is max(2 x current, 4), so keep max small | A patched status (correction 3) | Accept on the real controller, with usage set by pod annotations. Fallback, less real, only if 382 is refused: `--controllers=*,-horizontalpodautoscaling` and a patched status | a few KWOK pods | 382 | Low | Three HPAs in the three states. A scenario step pushes one to max and back | Sonnet |
| 767 kubeconfigs | `user.exec.interactiveMode: Always` makes kubectl refuse to run the plugin when stdin is not a terminal (Kubernetes authentication docs). tbot's `kubernetes/v2` output is an exec-plugin kubeconfig by default (`disable_exec_plugin: false`, tbot configuration reference) | A real browser sign-in plugin (kubelogin) is not installed and is not needed | Accept. Files: the token kubeconfig (exists); a group-writable one; one owned by another sim user; one owned by root (the accepted case); an exec plugin that prints an `ExecCredential`, mode 600; an `interactiveMode: Always` plugin for the browser case | none | none | Low | Owner and mode of each file asserted. kubectl works with the token and exec files, and fails with kubectl's own interactive-mode error for the browser case | Sonnet |
| 760 logs | KWOK `Logs` and `ClusterLogs` with `logsFile`, `previousLogsFile` and `follow`. The server honours `tailLines`, `limitBytes`, `timestamps`, `previous` and `sinceTime` (`pkg/kwok/server/debugging_logs.go` v0.8.0) and reads the files as kubelet (CRI) log files | Exec through Teleport stays a later spike | Accept logs, with a crash-loop pod that has a previous file. Describe already works. Exec and session recording stay in group B | none (KB of log files) | P1 | Low | `kubectl logs --tail=500 --timestamps --limit-bytes=N [--previous]` answers for the crash-loop pod as viewer. The limited user gets forbidden on `pods/log` | Sonnet |
| 765 Volcano | v1.16.0 (2026-10-14). `queues`: cluster-scoped, v1beta1 only, status subresource, state `Open`, `Closed`, `Closing` or `Unknown`, pending, running and inqueue counts. `podgroups`: namespaced, v1beta1, no status subresource, phase `Pending`, `Running`, `Unknown`, `Inqueue` or `Completed`, `minMember`, `queue` | No controller and no webhooks (fixtures only, as for Kueue) | Accept. Pin the v1.16.0 CRDs, with a README like `k8s/kueue/README.md` | none (CRDs) | 762 (asserts them) | Low | `api-resources --api-group=scheduling.volcano.sh` lists both. A Closed queue and a Pending PodGroup asserted. A step closes one and restores it | Sonnet |
| 766 GPU shapes | GPU Feature Discovery labels (device plugin v0.20.1 README and GFD README): MIG `single` overrides `gpu.product` (`<model>-MIG-<profile>`) and `gpu.count`; `mixed` gives `nvidia.com/mig-<profile>` resources and `nvidia.com/mig-<profile>.count` labels. Profiles per GPU from NVIDIA's MIG guide (updated 2026-11-04). H100 80GB: 1g.10gb, 1g.20gb, 2g.20gb, 3g.40gb, 4g.40gb, 7g.80gb. H200: 1g.18gb, 1g.35gb, 2g.35gb, 3g.71gb, 4g.71gb, 7g.141gb | The client reads only the strategy label, product, count, `gpu.present`, driver major, and `nvidia.com/gpu` capacity and allocatable. It does not read the `mixed` resources and treats `mixed` as no gap. MIG through DRA (partitionable devices, beta and on by default from 1.36) is not read by the client, so it is not built | Accept, in two parts. (a) Real kubelet version and two skew nodes, built with 762. (b) MIG `single` and `mixed` groups from `sim.yaml`, `gpu_unadvertised` and `gpu_advertised` steps, and fragmentation with the existing pod steps | none | 762 | Low | Labels and allocatable asserted per shape. The unadvertised step and its restore. The skew nodes asserted as newer and as too old | Sonnet |
| 768 scale | KWOK README (v0.8.0): "can reliably maintain 1k nodes and 100k pods" and "can create 20 nodes or pods per second". Pods with `spec.schedulingGates` stay Pending without scheduler work (stable from 1.30) | Our caps: etcd 256 MB, apiserver 384 MB. The `team-a` pod quota is 16 (ADR 0077) | Accept, measure first. Client facts: it keeps the first 2,000 nodes it parses, and skips pod lists above 10,000 pods. Measure 100, 500, 1,000 and 2,000 nodes, plus one run at 2,001. Put 10,001 gated pods in a dedicated namespace (about 9 minutes at 20 per second). The test is opt-in and never in `--full` | unknown; measuring it is the point of the item | 762 | High (host memory; the governor may refuse) | A memory and time table. `remainingItemCount` read above the guard. `plan` refuses an unsafe size with one message | Opus |
| 769 versions | Skew policy: kubectl within one minor of the API server; kubelet at most three minors older and never newer. `registry.k8s.io/kubectl` and `kube-apiserver` have v1.29.15 and v1.37.x tags | 1.29 is out of upstream support. `50-dra.yaml` uses `resource.k8s.io/v1` (GA in 1.34), so `bootstrap` would fail. The login image pins kubectl v1.36.1 and a test enforces it | Spike only. Predicted breaks: the DRA apply, kubectl skew on the k-login, the Kueue v1beta2 CRDs (unchecked), KWOK v0.8.0 at 1.29 (unchecked). The current release is 1.37: try it too | none | 762 | Medium | A report of which versions start and which reads answer. The default is unchanged | Sonnet |
| 752 Teleport memory | `docker stats` | none | Accept as written | none | none | Low | Idle and loaded figures recorded | Sonnet (owner-run) |
| 753 role tiers | Role v8 in 18.x: plural kinds, `api_group`, defaults per role version (roles reference) | Not proven on 18.11.8 | Keep demoted (group B) | none | 754 | Low | As in the issue | Opus |
| 756 kube faults | A missing binding, revoked impersonation, identity expiry | A proxy certificate change (rejected above) | Keep demoted | none | 753 | Low | As in the issue | Sonnet |
| 755 page view | The Teleport metrics reference names the kube counters | none | Keep demoted. Combine with the re-scoped 384, so one reader handles audit lines | none | 754 | Low | As in the issue | Sonnet |
| 759 Machine ID | Community Edition includes Machine ID issuance (feature matrix); `kubernetes/v2` output | HSM and TPM are not included | Keep demoted. Its kubeconfig is a real exec-plugin case for 767 | none (one-shot tbot on the host) | 753, 864 | Low | As in the issue | Sonnet |
| 757 separate agent | Agents dial the proxy | none | Keep deferred, with the gate. It needs 864's CA pin | +agent, gate 150 MB | 752, 864 | Medium | As in the issue | Opus |
| 758 Access Requests | Community Edition: role requests with `tsh request create`, approved with `tctl` on the Auth Service; no reviewer rules (role-requests guide) | Resource requests are Enterprise. A logged-in session is needed | Keep deferred. It becomes possible only if Q2 automates a profile | none | 763 | Low | As in the issue | Sonnet |
| 864 CA pin | none | none | Independent; do at any time. 757 and 759 reuse the pinned CA | none | none | Low | As in the issue | Sonnet |
| 384 API counter | `apiserver_request_total` (already built) | No count per client | Re-scope: count the read users (`viewer`, `limited`, and Teleport's impersonated users) from the API audit log at Metadata level. Or close it | none | none | Low | A per-user rate on the box | Sonnet |
| 385, 386 | none | none | Outside this batch. New k8s steps (the 382 stop step, 764, 765, 766) stay CLI-only until 386 lets the page play them | none | none | none | none | none |

### Build order
1. 762 with 766(a) (the real kubelet version), then 754.
2. 763; Q2 decides its scope.
3. P1, then 382, then 764. 760 (logs) after P1. 767 in parallel.
4. 765; 390 after Q4.
5. 766(b), the 769 spike, then 768 (needs Q3).
6. 752 at any time (owner-run); 864 at any time.
7. Group B: 753, 756, 755 (with 384), 759. 757 and 758 stay deferred.

### Questions for the owner (OWNER-REQUIRED)
- **Q1 Time-slicing (replaces question 1 above).**
  - Known: the client reads no sharing label. The device plugin has two shapes. With `renameByDefault=false`, it multiplies `nvidia.com/gpu`: capacity is GPUs x replicas, with the label `nvidia.com/gpu.replicas` and the product suffix `-SHARED`. With `renameByDefault=true`, it renames the resource to `nvidia.com/gpu.shared`. Allocatable `nvidia.com/gpu` is then 0 while `gpu.present=true`, so the client's "GPUs not advertised" rule would fire.
  - Not known: what the client should show for the renamed shape.
  - Recommendation: serve the `renameByDefault=false` shape in 766(b). Add the renamed shape as a separate fixture only if you say the client must handle it.
- **Q2 Profile path for 763 (replaces question 2).**
  - Known: Teleport documents no scripted local login. A script would drive `tsh login` in a pseudo-terminal, with a password and a code from a known OTP secret. That secret would be registered through the web UI's internal invite calls, which may change between Teleport versions.
  - Recommendation: a wire test with an identity file in the live tests. You run the profile path once per Teleport pin.
- **Q3 Scale target for 768 (replaces question 3).** Recommendation: keep 1,000 nodes as the bound and 2,000 as a stretch, with one run at 2,001 to show the client's node cap. Use 10,001 gated pods for the pod guard. Opt-in only.
- **Q4 Kubeflow CRDs (390, overlap).**
  - Known: the client lists `pytorchjobs.kubeflow.org` and `mpijobs.kubeflow.org` by group, without a version. The Training Operator v1 line (trainer v1.9.4) and mpi-operator (v0.8.2, `v2beta1`) both define `mpijobs.kubeflow.org`, so only one can be installed. Trainer v2 (v2.4.0) replaces both with `TrainJob`, which the client does not read.
  - Recommendation: PyTorchJob from the v1.9.4 line and MPIJob `v2beta1` from mpi-operator. Confirm both against their manifests in 390.
- **Q5 Version selection for 769.** Making 1.29 selectable needs a kubectl version argument in the login image, and DRA, Kueue and KWOK made conditional on the version. It changes SPEC C-2. Recommendation: spike only; no selectable old version unless you ask for one.

### Sources (read 2026-11-17)
- KWOK v0.8.0 (2026-06-23), from the tagged source https://github.com/kubernetes-sigs/kwok/tree/v0.8.0 :
  - docs: `site/content/en/docs/user/logs-configuration.md`, `metrics-configuration.md`, `resource-usage-configuration.md`, and `README.md`
  - `kustomize/metrics/resource/metrics-resource.yaml`
  - code: `pkg/kwok/server/debugging_logs.go`, `pkg/kwokctl/components/metrics_server.go`, `kwok_controller.go`, and `pkg/consts/consts.go`
- Kubernetes:
  - version skew policy https://kubernetes.io/releases/version-skew-policy/
  - exec plugin `interactiveMode` https://kubernetes.io/docs/reference/access-authn-authz/authentication/
  - HPA controller `pkg/controller/podautoscaler/horizontal.go` at v1.36.1
  - feature gates in kubernetes/website `feature-gates/`: `DRAPartitionableDevices` (beta and on by default from 1.36) and `PodSchedulingReadiness` (stable 1.30)
  - image tags from `registry.k8s.io/v2/<image>/tags/list`
- metrics-server releases (v0.9.1, 2026-10-29): https://github.com/kubernetes-sigs/metrics-server/releases
- Volcano v1.16.0 CRDs `config/crd/volcano/bases/scheduling.volcano.sh_queues.yaml` and `_podgroups.yaml`: https://github.com/volcano-sh/volcano/tree/v1.16.0
- Kubeflow releases: https://github.com/kubeflow/trainer/releases and https://github.com/kubeflow/mpi-operator/releases
- NVIDIA:
  - device plugin v0.20.1 README and `docs/gpu-feature-discovery/README.md`: https://github.com/NVIDIA/k8s-device-plugin/tree/v0.20.1
  - MIG profiles https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-mig-profiles.html
- Teleport (18.x docs):
  - feature matrix https://goteleport.com/docs/feature-matrix/ . Community Edition: SSO with GitHub only; role requests through the CLI only; Machine ID issuance included; licence free under 100 employees and 10 million USD revenue.
  - roles reference https://goteleport.com/docs/reference/access-controls/roles/ : v8, `max_sessions`, `max_connections` (Enterprise-only), `require_session_mfa`
  - Community role requests https://goteleport.com/docs/identity-governance/access-requests/oss-role-requests/
  - Machine ID for Kubernetes https://goteleport.com/docs/machine-workload-identity/access-guides/kubernetes/ and tbot configuration https://goteleport.com/docs/reference/machine-workload-identity/configuration/
  - audit storage and kube events: as in the Evidence list above
- Local, read-only: `tsh ssh --help`, `tsh login --help` and `tctl auth sign --help` on 18.11.2.

### Not verified (added)
- whether KWOK's server asks the API server for a client certificate (P1)
- the APIService routing without kube-proxy (382)
- whether the shared home lets the `ssh <host>` hop work without extra keys (762)
- Kueue and KWOK at 1.29 (769)
- the Kubeflow manifests (Q4)
- every live result in the table

## Addendum: Teleport audit helper, issue 754 (2026-11-17)
- **Where the log is read from.** `benchctl/teleportaudit.py` reads every `*.log` under `/var/lib/teleport/log` in the Teleport container with `docker exec` (the `dir` backend writes JSON lines, one file per day, under the data directory's `log/`; Teleport audit reference, Evidence 4). Host-side reading was not chosen because the files belong to the container's user; the host folder holds the same data. A missing folder raises an error that names it and fails the test; it never returns an empty log.
- **Matching.** Events are selected by `event`, a start mark (`now_minus`, 5 s in the past for clock difference) and exact field values; `response_code` may be a number or a string. The refused write uses a random pod name so only that call matches.
- **Recorded, not asserted:** what is written for a user with no Kubernetes group (the test asserts only that no 200 is audited for that user), the exact field set of 18.11.8, and the event types present in the log (`exec`, `port`, `session.*` expected absent without a pod shell; the exec spike is issue 760).
- **Not verified:** every live result of the new tests; that a refused write appears with 403 (expected: Teleport forwards, the API server refuses); the field names of 18.11.8 (docs example only).
- The helper is reused by 763 for the `tsh ssh` session events.

## Addendum: built, issues 762 and 766 part a (2026-11-17)
Decisions taken while building; each cites its source. Written and unit-tested; the live module has not been run (the owner runs it).
- **One catalogue of reads.** `benchctl/k8sreads.py` lists every read in this repository's words, with the outcome expected for the viewer and for the limited user, a classifier for one run's exit code and text, and a shape check for the answers a client parses. The live module `tests/real/test_k8s_reads_real.py` runs each read as both users from the k-login; the unit tests keep it read-only, complete against the viewer role (a new rule in `k8s/fixtures/40-rbac-cluster-viewer.yaml` needs a read or a stated reason) and in step with the table in `docs/guides/client-testing.md`, which is generated from it. The same catalogue can run through `tsh ssh` in 763.
- **Outcomes by exit code and text, not by wording of the whole message.** `kubectl auth can-i` prints `yes` or `no` (exit 1 for no). A refusal contains `forbidden`; a path the server does not route contains `NotFound` or "could not find the requested resource". Source: the behaviour of `kubectl` and the API server's status reasons (Kubernetes API concepts, error responses), and the existing assertions in `tests/real/test_k8s_real.py`.
- **Discovery is open.** `api-resources --api-group=...` is answered from API discovery, which every authenticated user may read (the `system:discovery` role), so both users see "served" for Kueue and "not served" for Volcano and Kubeflow. Marked in the catalogue; issues 765 and 390 flip those rows.
- **The metrics probe records, never fails.** Both 404 (today) and 200 (issue 382) are accepted for the viewer; the limited user may also get `forbidden`, because RBAC is checked before routing. The actual answer is printed.
- **`remainingItemCount`.** A list with `limit=1` and no selectors carries `metadata.remainingItemCount` when more items exist (Kubernetes API concepts, "Retrieving large results sets in chunks"); the shape check asks for an integer of at least 1 on the cluster-wide pod list. This is checked against documentation, not yet against 1.36.1 live.
- **The hop.** `ssh -o BatchMode=yes login2 kubectl ...` from the s-login as `alice`; the host name is the compose name of the k-login. A wrong host name must give exactly one line from ssh and exit 255. Whether the user's key works from the first node was the open point in the table above; if it does not, the live test fails with that sentence and a key fix is a separate change.
- **766 part a: the kubelet version is the API server's.** `node_manifest` sets `status.nodeInfo.kubeletVersion` to `k8s.version(sim)`, which is also the image tag of the API server, so `/version` and every node agree (`v1.36.1` by default; `sim.yaml` `kubernetes.version` moves both). KWOK's `node-initialize` stage keeps a given `kubeletVersion` and uses its own only when the field is empty (`k8s/kwok/stage-fast.yaml`, `or $nodeInfo.kubeletVersion $kwokVersion`). The cluster must be recreated (`benchctl down`, `up`) for an existing cluster's nodes to change, because the status of an existing Node is not rewritten by `kubectl apply`. The two skew fixtures (a newer node and one more than three minors behind) belong to part b and are not built here.
- **Not verified:** every live result of the new module; that `kubectl describe pod` as the limited user lists the pod's events without a cluster-wide rule (expected from the Role, not run); the hop's key distribution.

## Addendum: built, prerequisite P1, issue 872 (2026-11-17)
Written and unit-tested; the live tests are written and have not been run (the owner runs them). Sources were read at the tagged versions on 2026-11-17.
- **Node address.** `--node-ip` is the kwok-controller's fixed address (`addressing.service_ip("kwok-controller")`, 10.141.255.44), as kwokctl passes its own controller's address (`pkg/kwokctl/components/kwok_controller.go` v0.8.0). The stage templates' `NodeIP` and `NodeName` are the controller's flags, not the node's (`pkg/kwok/controllers/node_controller.go`), so every node advertises InternalIP 10.141.255.44 and Hostname `kwok-controller`, port 10247.
- **Correction to correction 2 above.** kube-apiserver v1.36.1 prefers `Hostname` first by default (`cmd/kube-apiserver/app/options/options.go`), and the Hostname address `kwok-controller` resolves through compose DNS, so the API server could already reach KWOK. `kubectl logs` failed because no `Logs` object existed ("no logs found"). The node IP mattered for metrics-server, which prefers InternalIP. The API server now sets `--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname`, kubeadm's value (`cmd/kubeadm/app/phases/controlplane/manifests.go` v1.36.1), so both callers use the same, fixed address.
- **Client certificate (open point above): KWOK asks for none.** `pkg/kwok/server/server.go` v0.8.0 serves TLS with `http.Server.ServeTLS` and no `TLSConfig`, so it never requests a client certificate, and the debugging handlers (logs, exec, metrics) do no authentication. kwokctl's `kube_apiserver.go` passes no `--kubelet-client-*` flags. We pass `--kubelet-client-certificate/-key` (the admin client certificate) anyway, as kubeadm does and a real kubelet requires; KWOK ignores it. No `--kubelet-certificate-authority` is set, so the API server does not verify KWOK's serving certificate (it is the admin certificate, client usage only). Live check: `test_kwok_serves_logs_without_asking_for_a_client_certificate`.
- **What KWOK serves.** `--enable-crds=Stage,Logs,ClusterLogs,Metric,ClusterResourceUsage`; `bootstrap` waits for all five definitions, then applies `k8s/kwok/logs.yaml` (a catch-all `ClusterLogs` over `k8s/kwok/logs/default.log`), `metrics-resource.yaml` and `usage-from-annotation.yaml` (both vendored unchanged from KWOK v0.8.0 `kustomize/metrics/`). Log files are kubelet (CRI) lines, `<RFC3339Nano> stdout|stderr F|P <text>`, mounted read-only at `/logs`; KWOK reads them with `k8s.io/cri-client/pkg/logs` (`debugging_logs.go`). A `Logs` object naming a pod wins over every `ClusterLogs`. Every node carries `metrics.k8s.io/resource-metrics-path: /metrics/nodes/<node>/metrics/resource` (KWOK resource-usage docs; metrics-server 0.7.0 or later reads it).
- **Existing clusters** keep their old address: `node-initialize` and the heartbeat stage keep `.status.addresses`. `benchctl down`, then `up`, recreates the nodes (as for 766 a). The annotation, being metadata, is applied to existing nodes.
- **Live tests written** (`tests/real/test_k8s_real.py`): every node's InternalIP is 10.141.255.44 with port 10247 and the annotation; `kubectl logs` as admin answers the canned text; the node proxy answers KWOK's resource metrics; a TLS request without a client certificate gets 200.
- **Not verified:** every live result above; that `selector: {}` on a `ClusterLogs` matches every pod (KWOK docs: both lists empty means all pods).
