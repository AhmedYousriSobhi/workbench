# Volcano objects without a controller (issue 765, ADR 0084)

- `crds.yaml`: the `Queue` and `PodGroup` definitions of Volcano v1.16.0 (`scheduling.volcano.sh/v1beta1`), copied unchanged from
  `config/crd/volcano/bases/scheduling.volcano.sh_queues.yaml` and `..._podgroups.yaml` of that tag, minus the Helm
  `resource-policy` annotation. To upgrade: download the new tag's two files, replace them here, re-run `tests/test_volcano.py`.
- `queues.yaml`: the objects: queue `gpu-batch` (Open, 2 running, 5 pending) and `maintenance` (Closed), PodGroup `train-run` (Running) and
  `train-wait` (Pending, minMember 4). `benchctl up --with k8s` applies the definitions, waits for them, applies the objects, then writes
  each Queue's `status` through the status subresource (`benchctl/volcano.py`). A PodGroup has no status subresource in v1beta1, so its
  status is stored by the plain apply.
- No controller and no new container run, so memory is unchanged and nothing admits a PodGroup. The scenario step `volcano` changes the
  state (`volcano-queue-closed`, SC-49).
