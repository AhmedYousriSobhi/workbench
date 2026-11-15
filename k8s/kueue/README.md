# Kueue objects without a controller (issue 469, ADR 0034 addendum)

- `crds.yaml`: the ClusterQueue, LocalQueue and ResourceFlavor definitions of Kueue v0.20.6, v1beta2 only (the release serves and
  stores nothing else; its v1beta1 and the conversion webhook were cut out, nothing else changed). To upgrade: download the new
  release's `manifests.yaml`, keep those three CRDs, drop the `conversion` block and any non-served version.
- `queues.yaml`: the objects. `benchctl up --with k8s` applies the definitions, waits for them, applies the objects, then writes
  each object's `status` through the status subresource (`kubectl patch --subresource=status`), because a CRD with a status
  subresource ignores `status` in a normal apply. `benchctl/kueue.py` does this.
- No controller runs, so the counts are fixtures: nothing admits a workload. The scenario step `queue` changes them.
