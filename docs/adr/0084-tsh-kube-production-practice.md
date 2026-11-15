# ADR 0084: How closely `tsh kube` follows production practice

Status: decided on the owner's delegation (2026-11-15), owner review open; decision issue 751, work items 752 to 760. Builds on [ADR 0083](0083-kubernetes-through-teleport.md) (issue 383) and [ADR 0020](0020-teleport-agentless-openssh.md); epic 471.

## Context
ADR 0083 made `tsh kube` work: Teleport 18.11.8 Community Edition, auth, proxy and a `kubernetes_service` in one process on port 3080, a static kubeconfig of a token user that may only impersonate, one role (`workbench-user`, role v7) for every user, bound to the read-only `workbench-viewer` ClusterRole; five live tests passed.

The owner now wants it "real, like working on a production cluster" and answered the open client question on 2026-11-15: **the monitoring client does not connect to Kubernetes today and has no Kubernetes feature.** So no client behaviour constrains this decision. The simulation is built as a production reference first and the client feature is developed against it afterwards. The decision is therefore scored on production fidelity, memory (a 16 GB workstation; the Teleport container cap is 320 MB, measured 222 MB before the Kubernetes Service), complexity and risk. The ADR-0034 text about how the client reads Kubernetes (kubectl over SSH) is a separate question, see "Questions for the owner".

Live checks for this ADR were **not run**: an owner-side `verify.sh --full --with-jira` was active, 22 containers were up and 4.9 GB was available (the brief asked for 6 GB). Every fact below is from public documents read on 2026-11-15, from ADR 0083's live results, or from local read-only `tsh` commands; each says which.

## Decision

### Production behaviours: reproduce or not

| # | Behaviour | Decision | Why (realism gain vs cost) | Work item |
|---|---|---|---|---|
| 0 | Measured memory with the Kubernetes Service | Do first | ADR 0083 left it blank; all later items are scored against it | 752 (S) |
| 1 | Role tiers viewer / team-a developer / admin, role v8, namespace and resource scoping, one RoleBinding per group | Accept | Largest realism gain, no memory, low risk. Production roles are per team and least privilege; a group without its own binding gives `Forbidden`. v8 is what the docs now show | 753 (M) |
| 2 | Audit visibility: assert `kube.request` events | Accept | Audit of every kubectl call by named user is Teleport's first Kubernetes claim; no memory (a file read) | 754 (S) |
| 2b | Show it in the page's Teleport view | Accept, after 754 | Operators watch the audit log and request rates; the page already reads Teleport's diagnostics and files | 755 (M) |
| 3 | Failure scenarios that need no new container: missing RoleBinding, revoked impersonation, expired identity, expired API certificate seen through Teleport | Accept | The failures a platform team meets daily; no memory | 756 (M) |
| 3b | "Proxy certificate change" | Reject for now | Every client path here uses `--insecure`; there is no CA-pinned flow to break. Revisit when a client pins the CA | none |
| 4 | Separate agent container that dials the proxy (reverse tunnel), agent-down and tunnel-lost faults | Accept, **gated**: built only if the agent's measured working set is at most 150 MB (cap 200 MB), else keep the co-located service | Reverse tunnel is the documented production default; direct dial is "legacy". Gain high for failure realism; memory cost unknown (not measured), so the gate decides | 757 (L) |
| 5 | Access Requests | **Spike only** | Community Edition has role requests through the CLI, approved by `tctl` on the Auth Service; resource requests (`kube_cluster`, `kube_resource`) and reviewer rules are Enterprise. Whether it can be driven without an interactive OTP is unknown | 758 (S) |
| 6 | Per-session MFA | Reject | "Per-session MFA cannot be satisfied by an OTP device" and only `kubectl` supports per-session WebAuthn for Kubernetes; the simulation is OTP-only (ADR 0020) | none |
| 7 | Machine ID (`tbot`) for automation identities | Accept | Production teams replace static Kubernetes tokens in CI with it; host-side one-shot, so no stack memory | 759 (M) |
| 8 | Several clusters through one `kubernetes_service` | Defer to epic 256 / issue 253 | Needs a second KWOK cluster (about 0.3 GB measured for one, ADR 0034). Keep the Teleport kubeconfig builder per cluster so it can follow | none |
| 9 | `kubectl exec`, logs, port-forward, session recording | **Spike only** | There is no kubelet, so no pod shell and none of the session events; KWOK has Exec, PortForward and Logs resources that might serve them | 760 (S) |
| 10 | Teleport pin | No change now | See "The pin and the Kubernetes RBAC advisory" | none |

### Order of work
1. 752 measure memory (S). Acceptance: the figure is in the budget table and ADR 0083.
2. 754 audit assertions (S) and 753 role tiers (M), in parallel. Acceptance: the live module asserts `kube.request` fields; developer writes only in `team-a`, viewer cannot write, a Teleport-level denial reads differently from a Kubernetes `forbidden`.
3. 760 exec/logs spike (S) and 758 Access Request spike (S). Acceptance: a written result in "Not verified" turned into "Verified live" or a stated reason it cannot be automated.
4. 756 failure scenarios (M). Acceptance: each fault has a restore step, an `expect` block and a different asserted message.
5. 757 separate agent (L), only if 752 and a measurement of the agent pass the gate.
6. 759 Machine ID (M), after 753 (needs a developer group).
7. 755 page view (M), after 754.

### Tool scope

A tool a future client would call is tested end to end in the simulation even though no client uses it yet (owner, 2026-11-15). "Tested" means a live test in `tests/real/test_teleport_kube_real.py` or its follow-ups.

| Tool | Status | Why |
|---|---|---|
| `tsh kube ls`, `tsh kube login` | supported / tested (ADR 0083, live) | The base of every flow |
| `tsh kube credentials` (the kubeconfig exec plugin) | supported / tested through kubectl (live); direct call and expiry added in 753 | The only way kubectl gets a certificate; hidden from the CLI reference, so verified on the installed `tsh` |
| `kubectl` with the kubeconfig `tsh kube login` writes | supported / tested | What most tools use |
| `tsh proxy kube` (local proxy, `--exec`) | supported / untested until 753 | Documented for clusters behind L7 load balancers; the one command that carries per-session MFA and, since 18.11.9, multiplexes requests; an app that wants a stable localhost endpoint would use it |
| `tsh kubectl` | supported / untested until 753 (happy path only) | A wrapper; on denial it tries to submit a resource Access Request, which is Enterprise |
| `tsh kube exec`, `tsh sessions ls --kind=kube` (`tsh kube sessions` is deprecated) | untested, depends on 760 | Needs a pod shell that KWOK does not give by default |
| `tsh kube join` | out of scope | Session sharing and moderation are not in Community Edition (feature matrix) |
| `tsh request` (role requests) | untested, depends on 758 | Community Edition has it for roles only |
| `tctl` | supported / tested inside the container (`docker exec`); from a client with an identity file it needs `--auth-server` | Admin tool, not a client tool |
| `tbot` | supported / tested in 759 | Non-interactive identity; local `tbot` 18.11.2 exists on the test host |
| Lens and other desktop tools reading a kubeconfig | untested by design | The Teleport FAQ says they work through the generated kubeconfig; a GUI cannot be driven here, the kubeconfig test is the proxy for it |
| Teleport Connect (`tshd`) | out of scope | Needs an interactive login (OTP or SSO) and a GUI; Connect sets `KUBECONFIG` per cluster, which the kubeconfig tests cover |
| Teleport web UI | served, not tested | Same port 3080; the audit log page is not exercised (see 755) |

## What a future client feature would use

Ranking is my inference from what an app that already uses `tsh login` and `tsh ssh` (ADR 0020) would do; it is not a client requirement. Sources are in "Evidence".

1. **`tsh` shelled out, then the kubeconfig and kubectl or client-go.** `tsh kube login` writes a kubeconfig whose credential is the exec plugin `tsh kube credentials`; client-go runs the same plugin. Most likely, because it mirrors the SSH path. Needs from the simulation: what exists, plus 753 for the credential command and tiers.
2. **`tsh proxy kube`.** A local listener the client points a plain kubeconfig at; avoids running the plugin on every request. Needs: 753.
3. **Identity file, then `tbot` for headless use.** Teleport tells non-interactive callers to set `TELEPORT_IDENTITY_FILE` and `--proxy`, and to use Machine ID when there is no identity file. The sim's identity files live 12 h by default; `tbot` renews. Needs: 759.
4. **Interactive `tsh login` (OTP; SSO is out of scope, ADR 0020).** Works for SSH today through `benchctl add invite`. Per-session MFA cannot work with OTP (rejected above).
5. **Teleport Connect's `tshd`, the web API, Application Access.** Lowest. I found no public document of a stable web API for Kubernetes access and Application Access is for web apps, not the Kubernetes API (not verified further).

Choices the client work must make, not decided here: shell out to `tsh` or call libraries; exec plugin or local proxy; identity file or an interactive login (and so who holds the OTP); where the kubeconfig lives (a private `KUBECONFIG`, never the user's `~/.kube`; ADR 0083's live module does this); which context name to use (the docs' default template is `{{.ClusterName}}-{{.KubeName}}`; the context name was not verified here); what to do when the certificate expires mid-session; whether namespace-limited users (the developer tier) must work.

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

## Rejected
- **Per-session MFA.** OTP cannot satisfy it; building WebAuthn needs a software authenticator nobody has shown to work headless here.
- **Resource Access Requests, reviewer rules, session sharing, dual authorization.** Not in Community Edition (feature matrix), and the licence note in ADR 0020 keeps us on Community.
- **Proxy certificate change fault.** No client path here verifies the proxy certificate.
- **A second Kubernetes cluster now.** Memory; belongs to epic 256.
- **Bumping the pin now.** Not affected by the advisory; the bump starts a new cluster.
- **Moving the Kubernetes Service into a new container unconditionally.** Gated on measured memory (757).

## Consequences
- ADR 0083 point 3 (v7 role, no `kubernetes_resources`, one group for all) is superseded by 753 once built: three tiers and v8.
- `docs/guides/teleport-kube.md`, `docs/guides/client-testing.md` and the progress tracker change with each work item, not with this ADR (decision only).
- A future client sees a cluster that behaves like a production Teleport-fronted one, except for the gaps below, which each guide must state: no pod service-account join, no pod shell unless 760 works, no Enterprise requests, no MFA per session, no SSO.
- Each work item has a live test; the decision is reviewed again after 752 and 757's measurement.

## Not verified
Marked with the issue that will verify it.
- That the pinned 18.11.8 accepts v8 roles (the docs say 18.x; 753).
- The audit fields and file location actually written by 18.11.8 for our setup, and whether `exec`, `port`, `session.*` appear without a pod shell (754, 760). `tctl get events` was not found in the reference.
- Memory of the Kubernetes Service in the Teleport container, and of a separate agent (752, 757).
- That a Compose container can join through the self-signed proxy with a pinned CA and keep a tunnel (757).
- `tsh proxy kube`, `tsh kubectl`, `tsh kube exec`, `tsh sessions` with an identity file (753, 760).
- `tctl bots add` and `tbot` Kubernetes output on the Community image; the feature matrix says Machine ID issuance is included, and the guide carries no edition note (759).
- `tsh request create --roles` with an identity file, and a non-interactive OTP (758).
- KWOK Exec/Logs through Teleport (760).
- That 18.11.8 contains the Kubernetes RBAC fix: inferred from release dates, not stated by Teleport and not tested.
- Teleport web UI Kubernetes pages and audit page in Community Edition; Lens behaviour (docs only).
- The default kubeconfig context name written by `tsh kube login` for our cluster.
- ExtraHop's and Rush Street's own case studies; any GPU or AI operator using `tsh kube` (none found).
- Licence thresholds for 2026 (ADR 0020 addendum; issue 249).

## Questions for the owner
1. ADR 0034's first paragraph says the client reads Kubernetes with `kubectl` over the SSH session it already has. Your 2026-11-15 answer says it has no Kubernetes feature. Which one is current: should ADR 0034 be corrected, or does "does not connect" mean "does not connect directly, only through SSH"? This changes nothing here, but it decides what the k-login documents say.
2. Gate for the separate agent (757): is 150 MB working set (cap 200 MB) the right ceiling, or would you spend more of the 16 GB on production topology?
3. Is Community Edition the permanent baseline? Access Request reviews, session sharing and resource requests are Enterprise; if the owner has an Enterprise licence for testing, items 5 and 9 change.
