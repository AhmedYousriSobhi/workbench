# 23. Alerts and dashboards

**You will:** see the default alert rules fire, and find the same data in Prometheus and Grafana. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)). Prometheus and Grafana come with it.

## Steps

1. Open Grafana at http://127.0.0.1:3000 (user `admin`, password `admin`; a closed local lab).

2. Open Prometheus at http://127.0.0.1:9090, then `/targets`. One target per node (job `sim-nodes`) and per part with metrics (`slurm-*`, `governor`, `jira-mock`, `k8s-*`, `kwok-controller`: about 21 with the Kubernetes part).

3. List what fires now. It prints `none` when nothing fires.

   ```bash
   ./scripts/benchctl get alerts
   ```

4. Make something fire. `node-down` takes 40 s and picks the CPU node `cn002`; `gpu-xid-storm` takes 180 s. Read the alerts (step 5) while each runs. `NodeDown` fires for it too, about 2 minutes after the node goes off.

   ```bash
   ./scripts/benchctl scenario run node-down
   ./scripts/benchctl scenario run gpu-xid-storm
   ./scripts/benchctl set node gpu002 off
   ./scripts/benchctl set node gpu002 on
   ```

5. Read the alerts again, as JSON for a script.

   ```bash
   ./scripts/benchctl get alerts --json
   ```

6. Give Grafana a job load to draw.

   ```bash
   ./scripts/benchctl load on --rate 6
   ./scripts/benchctl load off
   ```

## The seven default rules

- `GpuXidCritical`, `GpuTempHigh`, `InfinibandPortDown`
- `NodeDown`, `NodeDrained`, `ExporterDown`
- `JiraErrors`

They live in `observability/rules.yml`.

## You should see

- After `node-down`: `NodeDrained` and `ExporterDown`, first `pending`, then `firing` after their 30 s (they clear after the scenario). `NodeDown` fires for any node, CPU or GPU, within about 2 minutes of it going off. `GpuXidCritical` fires about 15 s into `gpu-xid-storm`, one per GPU node.
- Not exercised: the Grafana and Prometheus pages and the dashboards, only the endpoints (`/api/health` and `/api/user` answer with `admin`/`admin`).
- GPU dashboards follow the jobs: busy GPUs read 70 to 100 %, idle ones read idle, within about 20 seconds.

## Next

- [Practise a GPU incident](15-gpu-incident.md)
- [Using the cluster](../manual/03-using-the-cluster.md): the mocks and the dashboards.
