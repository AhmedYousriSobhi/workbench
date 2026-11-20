# Extra Kubernetes clusters: KWOK-only or virtual clusters (issue 248)

Status: research note, no decision. Related: ADR 0034 (KWOK first), ADR 0084 (what the client reads), issue 232
(privilege of kind and k3d), the plan section "Multiple clusters and the identity plane".

## Question
For clusters beyond the first, is a KWOK-only cluster (its own API server), a virtual cluster (vcluster, kcp, a shared
control plane with namespaces) or a second real server the better fit, under the 16 GB rule, for a client that runs
read-only `kubectl` over SSH (nodes, pods, events, namespaces, `auth can-i`, `/readyz`, `/version`, optional metrics API,
GPU node labels and `nvidia.com/gpu` capacity)?

## What was measured and what was not
- **Measured here (existing run, `docs/measurements/idle.json`, docker stats mean, 4 KWOK nodes):** kube-apiserver 243 MB,
  etcd 59 MB, controller-manager 57 MB, scheduler 25 MB, kwok controller 43 MB, metrics-server 20 MB. Sum without
  metrics-server about 427 MB; with it about 447 MB. (The architecture file says "about 0.3 GB without metrics-server",
  which does not match this file's sum; the two should be reconciled when the run is repeated. Process view: kube-apiserver
  RSS 314 MB.) One run, one node count; the per-fake-node cost is not measured.
- **Not measured:** vcluster, kcp, a second real kind or k3d server. No cluster was started for this note (another agent
  uses the cluster). None of the three projects publishes a per-cluster idle figure (checked below).

## Primary-source facts
| Option | What the source says | Footprint published? |
|---|---|---|
| KWOK (`kwokctl`, kwok.sigs.k8s.io, repo README) | `kwokctl` creates and manages clusters whose nodes are simulated by `kwok`; runs with Docker or Nerdctl or as binaries. "KWOK can reliably maintain 1k nodes and 100k pods"; "without significant consumption of CPU or memory" (marketing wording). Memory of the control plane is the real upstream components. | No per-cluster figure. |
| vcluster (vcluster.com docs, architecture and sizing 0.34) | Per virtual cluster: API server, controller manager, data store (embedded SQLite by default), syncer, optional scheduler in one container, plus CoreDNS pod. Runs as a pod on a host Kubernetes cluster (one namespace per virtual cluster), or as `vind` in Docker with no host cluster, or Standalone. With shared nodes the syncer copies pods to the host; with private nodes it syncs only control-plane state. Sizing page: smallest profile P1, 1 CPU and 2 GiB per replica limit (a limit, not a measurement); memory scales with object count, not node count. A third-party design page (not primary) says an idle one uses about 300 to 500 MiB. | Only limits and profiles; no idle RSS. |
| kcp (docs.kcp.io) | A control plane for many isolated "clusters" called workspaces; each is a Kubernetes-like HTTPS endpoint backed by logical clusters in one etcd with separate key ranges. The fetched pages do not say it serves Node or Pod; kcp is known as a control plane without container orchestration, but that was not confirmed on the pages read. | None. |
| Second real kind or k3d | Needs a privileged node container (ADR 0034, issue 232); real kubelet. | Estimate only (0.5 to 1 GB, plan). |

## Comparison against the 16 GB rule and the client
| Option | Memory per extra cluster | Client reads work? | Isolation (plan: share processes, never data) | Privilege | Fit |
|---|---|---|---|---|---|
| Second real kind or k3d | 0.5 to 1 GB (estimate) | yes, plus real logs | full | privileged (exception needed) | worst on memory and policy |
| KWOK-only, own API server (what ADR 0034 already builds) | about 0.43 GB measured (one run) | yes, as today; same fixtures, stages and faults | full: own API server, etcd, kubeconfig | none | proven here; memory is the real components, not fake nodes |
| vcluster in Docker (vind) or k3s-style, fake nodes | unknown, vendor limits 2 GiB; embedded SQLite saves etcd (59 MB here) | unverified: node objects, GPU labels, KWOK stages and `auth can-i` would have to be tested; fake nodes likely still need KWOK | full (own API server) | unknown for vind | would need a spike; saving at best etcd-sized |
| vcluster on a host cluster | needs a host cluster as well (our one KWOK cluster cannot run pods) | pods sync to a host with no kubelet | full | n/a | does not fit: no real nodes to host the syncer's pods |
| kcp workspaces | unpublished; one process for many | unverified: no confirmed Node, Pod, `/readyz` or kubectl core behaviour | logical, shared etcd | none | probably not a Kubernetes cluster the client could read as one |
| One shared API server with namespaces | near zero | nodes are cluster-scoped, so every "cluster" sees the same nodes; `get nodes` and `can-i` cannot differ per cluster | none, breaks the independence rule | none | rejected by the plan's independence principle |

## Reading
- The largest per-cluster items in a KWOK cluster are real upstream processes: apiserver (243 MB) then etcd, controller
  manager, kwok. Fake nodes are API objects, so node count is a minor cost (not measured). A vcluster would also run
  its own API server, so it saves at most the etcd and scheduler, not the apiserver, and adds a syncer and a host-cluster
  dependency.
- Two extra KWOK clusters cost about 0.9 GB on top of the first (one measurement); three KWOK clusters total are about
  1.3 GB, inside the P3 cap logic but over the current 1.0 GB cap for the one part. A per-cluster cap and the governor's
  admission (issue 243) need a number first.
- Savings candidates that keep full isolation and need no new technology: no metrics-server on extra clusters (20 MB),
  GOMEMLIMIT or apiserver flags to cut cache sizes (not tested), cold clusters stopped until needed (issue 244).

## Recommendation (for the owner to decide)
1. Rule: at most one real control plane (and none while #232 is unresolved); every extra cluster is KWOK-only with its own
   API server. It reuses code and tests that exist and keeps the independence rule.
2. Do not adopt vcluster or kcp now: no published idle figure, and the savings are bounded by the API server we would still
   run. Revisit only if a measured need appears.
3. Before writing a number into the plan table, measure offline, no new software: start a second `kwokctl`-style cluster
   (via the existing `benchctl/k8s.py` per-cluster naming, when it exists) and record RSS at 4, 50 and 500 fake nodes with
   `benchctl measure run`; that gives base cost and per-node cost. A vcluster spike (vind, 20 minutes) is optional.

## Open questions for the owner
- Accept "one real control plane at most, extras KWOK-only" as the rule, and is a vcluster spike wanted at all?
- Should the single 1.0 GB cap for part `k8s` become a per-cluster cap?

Sources: kwok.sigs.k8s.io and github.com/kubernetes-sigs/kwok; vcluster.com docs (architecture, sizing and performance 0.34);
docs.kcp.io (concepts, workspaces). Pages were summarised by a fetch tool, so quotes are from its extract.
