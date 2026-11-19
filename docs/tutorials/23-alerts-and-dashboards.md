# 23. Alerts and dashboards

**You will:** see the default alert rules fire, and find the same data in Prometheus and Grafana. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)). Prometheus and Grafana come with it.

## Steps

1. Open Grafana at http://127.0.0.1:3000 (user `admin`, password `admin`; a closed local lab).

2. Open Prometheus at http://127.0.0.1:9090, then `/targets`. One target per node and per part with metrics.

3. List what fires now. Empty means nothing fires.

   ```bash
   ./scripts/benchctl get alerts
   ```

4. Make something fire.

   ```bash
   ./scripts/benchctl scenario run node-down
   ./scripts/benchctl scenario run gpu-xid-storm
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

- `NodeDown` and `NodeDrained` after the first scenario, `GpuXidCritical` after the second.
- GPU dashboards follow the jobs: busy GPUs read 70 to 100 %, idle ones read idle, within about 20 seconds.

## Next

- [Practise a GPU incident](15-gpu-incident.md)
- [Using the cluster](../manual/03-using-the-cluster.md): the mocks and the dashboards.
