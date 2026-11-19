# ADR 0083: Kubernetes through Teleport (`tsh kube`)

Status: accepted by the owner (2026-11-15); issue 383. Reverses the 2026-11-14 decision in ADR 0034 ("not needed at the moment").

## Context
Real sites often put `tsh kube` in front of the Kubernetes API. Until now workbench reached Kubernetes only with `kubectl` over SSH from a login node. The owner reversed the earlier decision: `tsh kube` must work against the simulated Kubernetes API, implemented and tested.

## Decision
When both optional parts are selected (`benchctl up --with teleport --with k8s`), the existing Teleport container also runs Teleport's Kubernetes Service and registers the simulated cluster. No new container, no new capability, no root, no Docker socket, no new port.

1. **Same process, same port.** `kubernetes_service: {enabled: true, listen_addr: 0.0.0.0:3027, kubeconfig_file: /run/workbench/teleport/kube.kubeconfig}` is added to `teleport.yaml` only when the k8s part is selected (`benchctl/teleport.py`, `config(sim, kube=True)`). Config v3 multiplexes Kubernetes traffic on the web port 3080 (the proxy's `kube_listen_addr` stays unset), so the published ports are unchanged. The Kubernetes Service's own `listen_addr` (3027) is needed because Teleport 18 refuses to start a Kubernetes Service in this process without it ("set kubernetes_service.listen_addr if this process can be reached from a teleport proxy, or point teleport.proxy_server to a proxy to dial out"); it listens inside the container and is not published.
2. **A dedicated credential, not the admin certificate.** The kubeconfig is made at `up` from a new token user `teleport` (a row in `secrets/k8s/tokens.csv`, like `metrics`; an older cluster gets it without a new PKI). It embeds the CA and the API server's fixed address, so it holds no host path. A fixture, `k8s/fixtures/47-rbac-teleport.yaml`, gives that user only what Teleport's own `get-kubeconfig.sh` gives its service account: `impersonate` on users, groups and service accounts, `get` on pods, `create` on selfsubjectaccessreviews and selfsubjectrulesreviews.
3. **Role mapping.** *(Amended by ADR 0084 addendum, issue 753: the Kubernetes grant moved out of `workbench-user` into three tier roles, v8, one per access tier; the text below is the original v7 design, which the viewer tier keeps in effect.)* With the k8s part, the role `workbench-user` gains `kubernetes_labels: {"*": "*"}` and `kubernetes_groups: [workbench-k8s-viewers]`. A ClusterRoleBinding in the same fixture binds that group to the existing read-only `workbench-viewer` ClusterRole (the one the `viewer` user has; the built-in `view` role cannot read nodes). `kubernetes_resources` is left out: role v7 then allows every resource and verb at the Teleport level, and Kubernetes RBAC is what limits the user. A Teleport user whose role has no `kubernetes_groups` impersonates nothing and is refused.
4. **Ordering and changes.** Teleport's stage now starts after the Kubernetes stages (it reads the kubeconfig and registers the cluster at start) and still before the nodes (which read host certificates). Teleport reads its config and kubeconfig only at start, so `up` recreates a running Teleport container (`--force-recreate`) when either changed (the k8s part added or dropped, a new token). With the part off there is no `kubernetes_service` and no kubeconfig file.
5. **Not built:** per-user mapping to the `limited` user (`kubernetes_users`), Kubernetes access request flows, and a separate `kube_listen_addr`.

## Measured
Proven live on 2026-11-15 (`./scripts/verify.sh --unit teleport_kube`, 5 passed; four runs, each found something):
- The Kubernetes Service runs in the same process as auth and proxy, but only with `listen_addr` (the first run crash-looped without it).
- The cluster is registered under the kubeconfig context name, `workbench-k8s`; `tsh kube ls` lists it, and the published ports are still 3080 and 3434 only.
- `tsh kube login` with an identity file works, but kubectl runs `tsh kube credentials` as an exec plugin, which needs the same identity: set `TELEPORT_IDENTITY_FILE` (and `TELEPORT_PROXY`) in kubectl's environment, or it fails with "no SSH auth methods loaded".
- Role v7 without `kubernetes_resources` lets a viewer read the nodes (cluster-scoped), and a write is refused by Kubernetes RBAC as forbidden.
- A user whose role has no `kubernetes_groups` is denied; Teleport's message is "Your user's Teleport role does not allow Kubernetes access" (reported as NotFound).
- Teleport container memory with the service: to be recorded here from `docker stats` (limit 320 MB, unchanged).
- Still not tested: any client of the monitoring software; what it expects from `tsh kube` (cluster name, login method, Kubernetes user, read-only or more) is an open question for the owner.

## Licence note (addendum to ADR 0020)
Teleport Community Edition is under a commercial licence from version 16. Teleport announced in 2024 that it is free for personal use and for companies under 100 employees and 10 million USD revenue; those thresholds are not confirmed for 2026 and must be checked before any wider use. The Kubernetes Service is part of the same Community Edition binary, no extra component is added.

## Consequences
- SPEC C-2 and C-16 drop "Kubernetes through Teleport" from what is not built. ADR 0034's decision line points here.
- The module `tests/real/test_teleport_kube_real.py` covers the green path (list, login, read nodes as a viewer, a write refused) and the red path (no Kubernetes group).
