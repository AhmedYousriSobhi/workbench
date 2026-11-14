# ADR 0076: DRA ResourceSlices on the KWOK GPU nodes

Status: accepted by the owner on 2026-11-14 (issue 547, epic 557). Built; the live run is pending.

## Context
Current GPU clusters hand GPUs to pods through Dynamic Resource Allocation (DRA): a vendor driver publishes `ResourceSlice` objects, a `DeviceClass` selects them, and a pod's `ResourceClaim` is allocated by kube-scheduler. The KWOK nodes only advertised the `nvidia.com/gpu` extended resource, so nothing here could show a client, or the config lab, a DRA cluster.

## Decision
- `benchctl/dra.py` writes one `DeviceClass` (`gpu.sim.test`, a CEL selector on driver `gpu.sim.test`) and, per KWOK GPU node, one `resource.k8s.io/v1` `ResourceSlice` with one device per GPU. No driver runs: the objects go straight to the API server, so the real scheduler does the allocation.
- Device attributes: `uuid`, `model`, `index`, `nvlinkDomain` (8 GPUs per domain); capacity `memory`. The UUID is `hwinventory.gpu_uuid(node, i)`, the `UUID` label the GPU exporter puts on the same node's DCGM series, so a claim's device can be matched to a metric.
- Generated at `up` into `compose/generated/k8s/50-dra.yaml` (gitignored, rewritten each time, like `nodes.yaml`) and applied after the fixtures. The issue named `k8s/fixtures/50-dra.yaml`; a tracked file would be a generated file under source control, which the repository avoids (CLAUDE.md, conventions). The tracked part is `k8s/fixtures/55-dra-claims.yaml`: a `ResourceClaimTemplate` `one-gpu` and a pod `dra-train` in `team-a`.
- DRA is GA in Kubernetes 1.34 and later (`resource.k8s.io/v1`), and the pinned version is v1.36, so no feature gate is set. Partitionable devices (MIG slices in a slice) are not used; ADR 0075 stays Slurm-only.

## Not verified yet
`./scripts/verify.sh --unit k8s`: that the API server serves `resource.k8s.io/v1` on the pinned version, that the scheduler allocates the claim of `dra-train`, and that the allocated device's UUID is a series of the GPU exporter. The slice shape follows the documented v1 schema; the live run is the proof.

## Consequences
- One pod and one template more in `team-a`. A cluster with no GPU node gets no slice and no class.
- Slices are static: a node taken down in a scenario keeps its slice (a real driver would withdraw it). Not modelled.
