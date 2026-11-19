# 15. Practise a GPU incident

**You will:** play a GPU failure and follow it through the alert, the drain and the ticket, as on call. About 15 minutes.

## You need

- A running cluster with the Jira mock: `./scripts/benchctl start --with jira`.

## Steps

1. Read what the incident does, without running it.

   ```bash
   ./scripts/benchctl scenario run gpu-xid-storm --dry-run
   ```

2. Play it.

   ```bash
   ./scripts/benchctl scenario run gpu-xid-storm
   ```

3. Read the alerts Prometheus fires. `GpuXidCritical` is the first.

   ```bash
   ./scripts/benchctl get alerts
   ```

4. See which nodes drained, and why.

   ```bash
   ./scripts/benchctl get nodes
   ./scripts/benchctl describe node gpu001
   ```

5. See the injected faults.

   ```bash
   ./scripts/benchctl get faults
   ```

6. Open the dashboards: Grafana at http://127.0.0.1:3000 (user `admin`, password `admin`) and Prometheus at http://127.0.0.1:9090.

7. Put everything back.

   ```bash
   ./scripts/benchctl clear faults
   ```

## You should see

- The GPUs of both GPU nodes show XID 79 in `nvidia-smi` and DCGM, and the BMC logs a critical event.
- The node health check drains each node with a reason starting `NHC:`, and a ticket is filed.
- At the end the node health check resumes the nodes it drained.

## Other drills

- `fan-failure`: fans fail, the node runs hot, the BMC log goes critical.
- `ib-port-down`: an InfiniBand port goes down and another flaps.

## Next

- [Alerts and dashboards](23-alerts-and-dashboards.md)
- [Write a health check](16-write-a-health-check.md)
