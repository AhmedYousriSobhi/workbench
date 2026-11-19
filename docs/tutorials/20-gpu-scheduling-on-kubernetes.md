# 20. GPU scheduling on Kubernetes

**You will:** play the GPU scheduling problems an AI platform team meets: fragmentation, preemption, MIG and closed queues. About 15 minutes.

## You need

- The Kubernetes part with two GPU nodes: `./scripts/benchctl up --with k8s`. Check memory first with [`plan`](02-size-your-cluster.md).

## Steps

1. Fragmentation: 4 GPUs are taken on each GPU node, then a pod asking for 5 waits `Pending` although enough GPUs are free in total.

   ```bash
   ./scripts/benchctl scenario run k8s-gpu-fragmentation
   ```

2. Preemption: fillers hold a node, and the real scheduler evicts them for an inference pod.

   ```bash
   ./scripts/benchctl scenario run k8s-preempt
   ```

3. MIG: a GPU node becomes a MIG node, first with strategy `single`, then `mixed`.

   ```bash
   ./scripts/benchctl scenario run k8s-mig-single
   ./scripts/benchctl scenario run k8s-mig-mixed
   ```

4. A node stops advertising its GPUs, although its labels still say it has them.

   ```bash
   ./scripts/benchctl scenario run k8s-gpu-unadvertised
   ```

5. A Kueue queue goes inactive, and a Volcano queue closes.

   ```bash
   ./scripts/benchctl scenario run k8s-queue-inactive
   ./scripts/benchctl scenario run k8s-volcano-queue-closed
   ```

6. A drain blocked by a disruption budget, and the autoscaler pushed to its maximum.

   ```bash
   ./scripts/benchctl scenario run k8s-drain-blocked
   ./scripts/benchctl scenario run k8s-hpa-max
   ```

## You should see

- Read each effect with `kubectl` from the login node ([tutorial 8](08-use-kubernetes.md)): `get pods -A`, `describe pod`, `get events -A`, `get clusterqueues`.
- Each scenario restores itself.

## Remember

- Pods never really run, and Kueue and Volcano have no controllers here: their status is written by the scenario.
- The scheduler, the autoscaler and the disruption budget are real.

## Next

- [Use Kubernetes](08-use-kubernetes.md)
- [Use Teleport](09-use-teleport.md)
