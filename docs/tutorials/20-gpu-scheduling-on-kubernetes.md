# 20. GPU scheduling on Kubernetes

**You will:** play the GPU scheduling problems an AI platform team meets: fragmentation, preemption, MIG and closed queues. About 15 minutes.

## You need

- The Kubernetes part with two GPU nodes (the default): `./scripts/benchctl up --with k8s`. Check memory first with [`plan`](02-size-your-cluster.md).

## Steps

- Each scenario waits until it ends, prints `scenario passed`, then restores itself. Run them one at a time.
- Read the effect with `kubectl` in a second terminal.
- Times: 180 s for steps 1 and 2; 120 s for steps 3 and 4 and the Volcano scenario; 150 s for the Kueue one; 100 s for the drain; 300 s for the autoscaler (about 22 minutes in all).

1. Fragmentation: 4 GPUs are taken on each GPU node, then a pod asking for 5 waits `Pending` although enough GPUs are free in total. From 20 s on, `kubectl get pods -A` lists `frag-wait` Pending and `kubectl get events -A` has `FailedScheduling ... 4 Insufficient nvidia.com/gpu`.

   ```bash
   ./scripts/benchctl scenario run k8s-gpu-fragmentation
   ```

2. Preemption: fillers hold a node, and the real scheduler evicts them for an inference pod. After about 30 s `urgent` is `Running` in `team-b` and `kubectl get events -A` has `Preempted ... Preempted by pod ...` for a `fill-` pod.

   ```bash
   ./scripts/benchctl scenario run k8s-preempt
   ```

3. MIG: a GPU node becomes a MIG node, first with strategy `single`, then `mixed`. Read it with `kubectl get node kgpu001 -o json | grep nvidia.com`: `single` gives `gpu.product` `NVIDIA-H200-MIG-1g.18gb` and `nvidia.com/gpu: 7`; `mixed` gives `nvidia.com/mig-1g.18gb: 7` and `nvidia.com/gpu: 7` (one fewer than the 8).

   ```bash
   ./scripts/benchctl scenario run k8s-mig-single
   ./scripts/benchctl scenario run k8s-mig-mixed
   ```

4. A node stops advertising its GPUs, although its labels still say it has them: the same `kubectl get node kgpu001 -o json | grep nvidia.com` shows `nvidia.com/gpu.count: 8` but `nvidia.com/gpu: 0`.

   ```bash
   ./scripts/benchctl scenario run k8s-gpu-unadvertised
   ```

5. A Kueue queue goes inactive, and a Volcano queue closes. Read them with `kubectl get clusterqueue gpu-training -o jsonpath='{.status.conditions}'` (`ClusterQueue is stopped`, `Active` False) and `kubectl get queues.scheduling.volcano.sh gpu-batch -o jsonpath='{.status.state}'` (`Closed`, then `Open` at 120 s) with `kubectl get podgroups -A` (`train-run` goes `Pending`).

   ```bash
   ./scripts/benchctl scenario run k8s-queue-inactive
   ./scripts/benchctl scenario run k8s-volcano-queue-closed
   ```

6. A drain blocked by a disruption budget, and the autoscaler pushed to its maximum. During the drain `kubectl get nodes` shows `kgpu001` as `Ready,SchedulingDisabled` and the inference pods stay (as `alice` you cannot list disruption budgets: `Forbidden`). After about two minutes of the autoscaler scenario `kubectl get hpa -A` shows `hpa-below` at `4/4` replicas, `cpu: 400%/50%`.

   ```bash
   ./scripts/benchctl scenario run k8s-drain-blocked
   ./scripts/benchctl scenario run k8s-hpa-max
   ```

## You should see

- Read each effect with `kubectl` from the login node ([tutorial 8](08-use-kubernetes.md), SSH port 2222): `get pods -A`, `get events -A`, `get node kgpu001 -o json`, `get clusterqueues`, `get hpa -A`.
- Each scenario restores itself.

## Remember

- Pods never really run, and Kueue and Volcano have no controllers here: their status is written by the scenario.
- The scheduler, the autoscaler and the disruption budget are real.

## Next

- [Use Kubernetes](08-use-kubernetes.md)
- [Use Teleport](09-use-teleport.md)
