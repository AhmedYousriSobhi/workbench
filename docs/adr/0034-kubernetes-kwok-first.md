# ADR 0034: Kubernetes starts as a real API server plus KWOK nodes

Status: accepted as a start (issue 232, SPEC C-2); the owner expects to extend it, see "Shape of the cluster"

## Context
SPEC C-2 asks for k3d/kind (1 server + 1 real agent) plus KWOK fake nodes. Two facts change the order of work.

1. **What the client reads.** The desktop monitoring client uses plain `kubectl`, read-only, run over the SSH session it
   already has (on the login node, or on a "Kubernetes host" it reaches by `ssh`). It needs nothing installed in the cluster.
   It reads: `get nodes`, `get pods`, `get events`, `get namespaces`, `get --raw /readyz?verbose`, `/version`, the
   metrics API (`/apis/metrics.k8s.io/v1beta1/nodes`, optional), `auth can-i`, `config view --minify`, and on a click
   `describe pod` and `logs`. With "workload signals" on, also Deployments, Jobs, PVCs, DaemonSets, ResourceQuotas and
   HorizontalPodAutoscalers. It uses node labels (`nvidia.com/gpu.product`, `nvidia.com/gpu.count`,
   `nvidia.com/gpu.present`, `nvidia.com/mig.strategy`, an optional pool label), `nvidia.com/gpu` capacity and allocatable,
   and the kubeconfig file's owner and mode (it refuses a loose one). Nothing needs a kubelet, a container runtime or a
   real GPU. Source of this list: the client's own Kubernetes documentation and command scripts, read-only.
2. **Privilege.** k3d and kind run their node as a privileged container. workbench containers must stay unprivileged
   (CLAUDE.md, `benchctl doctor`). A real agent node would need an exception.

## Decision (proposed)
- **Phase 1 (this ADR): real control plane + KWOK nodes, no real agent.** One KWOK cluster (`kwokctl create cluster
  --runtime docker`) gives a real `kube-apiserver`, `etcd`, controller-manager and scheduler, and a `kwok` controller that
  plays the nodes and pods. No privileged container. This serves every read in point 1 with real API behaviour
  (RBAC, `auth can-i`, `/readyz`, events, describe).
- **Real agent deferred.** SPEC C-2's "1 real agent" is not built in phase 1. It is revisited only if the client needs
  something a fake node cannot give (real pod logs from a running container, for example). If it is built, it needs its
  own ADR for the privileged-container exception. This changes SPEC C-2 and needs the owner's approval.
- **Optional part `k8s`, off by default**, in the parts registry (`PART_INFO`/`PARTS`), category *workload*, priority P3.
  Budget is a planning figure until measured (architecture file 04 says 1.2 GB for k3d plus KWOK; a KWOK control plane
  is expected to be lower: etcd + apiserver + controller-manager + scheduler + kwok). Phase 1 measures it and replaces the
  number (ADR 0027 rule: plan against measured memory).
- **Nodes from `sim.yaml`.** A `kubernetes:` block (node count, GPUs per node, GPU type) is the single source. Fake nodes
  are named like the Slurm nodes where they overlap, carry `nvidia.com/gpu` capacity and allocatable, the GPU Feature
  Discovery labels for the GPU type, and a `pool` label. Node names agree everywhere (SPEC section 3).
- **Fixtures.** `k8s/fixtures` (namespaces, workloads, failure signals, viewer RBAC) are applied after the cluster is up.
  A KWOK `Stage` set makes pods Running, CrashLoopBackOff, Pending (unschedulable, waiting for GPU), OOMKilled and
  ImagePullBackOff, and writes Warning events (evictions).
- **Client access.** `kubectl` is added to the `node-base` apt/binary layer only through the rules in ADR 0014 (login image
  uses node-base). A read-only kubeconfig (the viewer role, token based, mode 600, owner the simulated user) is generated
  into `compose/generated/secrets/k8s` and mounted at `~/.kube/config` on the login node, so the client's Discover step
  finds it. The API server is published on 127.0.0.1 only and has a fixed private IP from the addressing plan (ADR 0018,
  new service address in 10.141.255.0/24). No admin kubeconfig reaches the login node.
- **Faults.** Scenario target `k8s` (the engine already skips it): `node_notready`, `node_ready`, `cordon`,
  `evict`, `pod_crashloop`, `pod_oom`, `pod_pending_gpu`, `clear`. Each is a `kubectl` call through the scenario
  engine on the host (ADR 0006), so the web page keeps no Docker access (W-3). Assertions: `k8s.node_ready`, `k8s.pod_phase`.
- **Governor.** The KWOK containers carry `workbench.class=P3` and a `workbench.node` label where relevant. The governor
  freezes or stops by container name, as for every other part. Stopping the control plane is a fault the client sees as an
  API server not ready.
- **Metrics API.** metrics-server is out of phase 1 (the client treats it as optional and shows nodes without it).
  Revisit as a separate issue.

## Consequences
- Kubernetes becomes testable by the client without a privileged container and at a smaller memory cost than k3d.
- SPEC C-2 and `client-testing.md` change from "not built" to "built, KWOK only; no real agent, no real pod logs".
- No real kubelet behaviour: no real pod logs (KWOK can serve canned logs through its `Logs` resource), no real exec or
  port-forward. The client never uses the last two.
- A second cluster (issue 248, 240) is out of scope here; the part is shaped to be per cluster later.

## Phase 1 plan (after approval)
1. Add `kwokctl`, `kubectl` as pinned, checksum-verified downloads (user-local for the host, in the image for the login
   node), no sudo.
2. Measure the idle KWOK control plane and set the budget.
3. Registry entry, compose service(s) or a `benchctl` helper that wraps `kwokctl` (it creates its own containers; the
   governor labels are applied after creation), `sim.yaml` `kubernetes:` block.
4. Node, Stage and fixture generation; read-only kubeconfig; login node wiring.
5. Scenario target and steps; `tests/real/test_k8s_real.py` (nodes Ready, GPU capacity, NotReady fault seen by the viewer
   kubeconfig from the login node, `auth can-i` yes/no).
6. Docs: SPEC C-2, manual, `client-testing.md`, `progress.yaml`, getting-started; then try the client against it.

## Shape of the cluster (owner direction, design constraints for phase 1 and later)
Real clusters are mixed, so the Kubernetes part must not assume "one cluster = Slurm only" or "one cluster = Kubernetes only".
- **Heterogeneous single cluster.** Nodes have a role: some are Slurm nodes, some are Kubernetes nodes, some may be both.
  `sim.yaml` carries the role per node group (a `scheduler:` attribute: `slurm`, `k8s`), so node names and counts agree
  everywhere (SPEC section 3). Phase 1 adds the `k8s` role next to the existing Slurm nodes; it changes none of them.
- **Two kinds of login node.** An `s-login` (Slurm client tools, today's `login`) and a `k-login` (`kubectl` and the
  read-only kubeconfig) in the same cluster. This extends C-18 (`login:` entries get a `scheduler:` of `slurm`, `k8s` or
  `both`), and the client profile export lists each with the scheduler it serves. Phase 1 puts `kubectl` and the
  kubeconfig on a `k-login`, and leaves the existing login unchanged.
- **One controller.** As in a DGX system, one head node (bcm-mock, the BCM head node) is the control point for the whole
  cluster; the other nodes are changed by Ansible playbooks. bcm-mock lists the Kubernetes nodes and their category in
  `device; list` and `category; list` (C-4). An Ansible control path is decided in ADR 0082 (it runs on the controller node) and still to be built (issue 389).
- **Portal UI and Horovod.** A portal UI and Horovod (MPI jobs) sit on top of Kubernetes and Slurm. They are later issues.
  Phase 1 keeps the API, the node labels and the namespaces they need (Kubeflow-style `MPIJob` CRD, a portal namespace)
  possible, and does not build them.
- **Multiple clusters.** Everything above is defined per cluster, not globally, so issues 240 and 248 can add more
  clusters later. Names, addresses (ADR 0018) and secrets are per cluster.
- **As real as possible.** Real components wherever they are free of the privilege and memory cost: the real API server,
  etcd, scheduler, controller-manager, `kubectl` and the real CRDs and RBAC. Only the kubelet side is simulated (KWOK).
  The same rule applies to every tool added later: pinned upstream binary or image, no re-implementation.

## Decisions on the questions raised
- SPEC C-2 changes to "KWOK first, real agent deferred": approved by the owner as a start; it may be modified later.
- One Kubernetes cluster now, but shaped as above (heterogeneous, per cluster, multi-cluster possible later).
- Kubernetes through Teleport (`tsh kube`): first decided 2026-11-14 as not needed; **reversed by the owner on 2026-11-15** and built (issue 383,
  [ADR 0083](0083-kubernetes-through-teleport.md)): Teleport's Kubernetes Service registers this cluster when both parts are selected.

## As built (phase 1, issue 232)
Decided with the owner while building; each can be changed later.
- **Compose services, not `kwokctl`.** `kwokctl create --dry-run` shows the cluster is five upstream images started with plain
  Docker flags. They run as compose services (`benchctl/k8s.py`): our uid (not root), memory limits, the `workbench.class=P3`
  label, fixed addresses from the plan (`10.141.255.40` to `.44`), our own PKI and kubeconfigs in `secrets/k8s`. No host tool is
  installed: `kubectl` runs from `registry.k8s.io/kubectl` (and is copied into the login image from it).
- **Measured** (4 KWOK nodes and the fixtures): apiserver 201 MiB, etcd about 130 (it sat at its first 128 MB cap, now 256),
  controller manager 47, scheduler 18, KWOK 11. Caps total 896 MB. None of the containers is privileged and none needs a
  capability (`cap_drop: ALL` stops the apiserver binary, which carries file capabilities, so it is not used).
- **Failures come from KWOK stages.** With no kubelet every pod would be Running. A pod labelled `workbench_fault`
  (`crashloop`, `oom`, `imagepull`, `jobfail`) gets the status a kubelet reports (`k8s/kwok/stage-faults.yaml`); the default
  `pod-ready` stage skips labelled pods. `Unschedulable` and the Warning events are the real scheduler's. The KWOK controller starts
  after the cluster content exists (it exits with "no stages found" otherwise) and needs `--enable-crds=Stage`.
- **NotReady is a KWOK stage.** The scenario step `down` labels the node `workbench_fault=down`; the `fault-node-down` stage
  sets Ready to False (`KubeletNotReady`) and the default `node-initialize` stage skips labelled nodes, so it stays. `up` removes
  the label and `node-initialize` makes the node Ready again. First tried and dropped: removing the KWOK annotation does not stop
  KWOK renewing the node's lease, so the node controller never marked it Unknown (seen live). KWOK manages only nodes with the
  annotation, so a real node can join the same API server in phase 2.
- **Access.** A static token file gives two users: `viewer` (a read-only ClusterRole, cluster-wide) and `limited` (read of pods and
  events in `team-a`). Every login node gets both kubeconfigs and `login.sh` installs them in each simulated user's `~/.kube`
  (owned by the user, mode 600, the check a client makes). The administrator's certificate stays on the host.
- **s-login and k-login (issue 380).** A login entry is `MODE[:KIND]`, KIND `slurm`, `k8s` or `both` (default, as before). An s-login
  has no kubeconfig mount; a k-login has no `/etc/slurm` mount and does not wait for slurmctld; a k-login needs the `k8s` part (the
  CLI and the sheet turn it on). It is an attribute of the login entry, not a separate image. The diagram groups the Kubernetes nodes
  apart from the Slurm nodes (the page reads their state from the API as the read-only user), and the graph has `login>k8s-nodes:kubectl`
  next to `login>nodes:jump`. Printed ssh commands carry `-o IdentitiesOnly=yes`: with several keys in ssh-agent, sshd ended the login
  after six offers.
- **Node names** are separate from the Slurm nodes (`kcn001`, `kgpu001`); `--k8s-nodes N` sets the GPU node count.
- **Phase 2 candidates:** one real k3s or kind worker for real CPU pods and pod logs (privileged container, own ADR),
  metrics-server, Kubernetes through Teleport, a k-login node, the Infrastructure architecture diagram.

## Addendum: Kueue as definitions and status fixtures, no controller (issue 469, owner decision 2026-11-14)

The client reads queue status with `kubectl get`, only when `kubectl api-resources` lists the definition: `clusterqueues.kueue.x-k8s.io`
(`.status.pendingWorkloads`, `.status.admittedWorkloads`, the `Active` condition) and `localqueues` (pending and admitted counts).

- **Built:** the ClusterQueue, LocalQueue and ResourceFlavor definitions of Kueue v0.20.6 (released 2026-11) and a set of objects
  (`k8s/kueue/`): three active ClusterQueues (`gpu-training`, `gpu-inference`, `cpu-batch`), one inactive (`legacy-h100`, it names a
  missing flavor, reason `FlavorNotFound`), LocalQueues in `team-a` and `team-b`. The `viewer` user may read them
  (`k8s/fixtures/46-rbac-kueue.yaml`). **No controller and no new container**: the counts are fixtures, nothing admits a workload.
- **One version, no webhook.** Upstream serves v1beta2 from v0.15, stores it from v0.16 and stops serving v1beta1 at v0.20
  (checked in the v0.20.6 `manifests.yaml`: v1beta1 `served: false`). The file keeps only v1beta2 and drops the `conversion`
  block, so the API server needs no conversion webhook (v0.20.6 itself fixes the stale webhook reference of an unserved version).
- **Status** of a CRD with a status subresource is ignored by `kubectl apply`, so `benchctl/kueue.py` writes it with
  `kubectl patch --subresource=status` after the objects exist (`bootstrap_kueue` in `benchctl/k8s.py`, after the DRA step).
  The scenario step `queue: {name, active, pending}` rewrites a ClusterQueue and its LocalQueues the same way
  (`k8s-queue-inactive`).
- **Not built (owner decision):** kube-state-metrics and DCGM series for KWOK nodes; the Kueue controller and its Workload objects.
  Volcano and Kubeflow (pytorchjobs, mpijobs) definitions are not added: their CRDs are large and several versions, and the client
  only looks for them; say if the evidence needs them.
- **Upgrade:** `k8s/kueue/README.md`.

## Pointer: group 4 decisions (issue 867, 2026-11-17)
[ADR 0084, group 4 addendum](0084-tsh-kube-production-practice.md#addendum-group-4-decisions-issue-867-2026-11-17) changes three phase 1 choices:
- **Logs and the metrics API need a reachable node address.** KWOK runs with `--node-ip=127.0.0.1`, so neither the API server (`kubectl logs`) nor metrics-server can reach its kubelet port. Prerequisite P1: the kwok-controller's fixed address, and more KWOK definitions enabled (`Logs`, `ClusterLogs`, `Metric`, `ClusterResourceUsage`). **Built in issue 872** (see the ADR 0084 addendum "built, issue 872"): nodes now advertise 10.141.255.44, and KWOK serves canned CRI log files and the kubelet resource metrics.
- **Nodes report a real kubelet version**, the API server's, instead of `kwok-<version>`, so a client can judge version skew.
- **The Kueue pattern** (definitions plus status, no controller) fits Volcano. It does not fit HPAs: their controller runs in the controller manager, so HPAs need the metrics API.
