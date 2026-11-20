# 15. Practise a GPU incident

**You will:** play a GPU failure and follow it through the alert, the drain and the ticket, as on call. About 15 minutes.

## You need

- A running cluster with the Jira mock: `./scripts/benchctl start --with jira`.

## Steps

1. Read what the incident does, without running it.

   ```bash
   ./scripts/benchctl scenario run gpu-xid-storm --dry-run
   ```

2. Play it. It takes 180 s and prints `scenario passed`; the GPU fault is cleared at 180 s. Do steps 3 to 6 in a second terminal while it runs (the alert fires after about 15 s).

   ```bash
   ./scripts/benchctl scenario run gpu-xid-storm
   ```

3. Read the alerts Prometheus fires. `GpuXidCritical` fires first, one per node, severity `page`; `NodeDrained` follows as `pending`.

   ```bash
   ./scripts/benchctl get alerts
   ```

4. See which nodes drained, and why. `get nodes` still says `running` (that is the container); `describe` shows `IDLE+DRAIN`, reason `NHC: GPU XID 79` and `XID 79` on the GPU line.

   ```bash
   ./scripts/benchctl get nodes
   ./scripts/benchctl describe node gpu001
   ```

5. See the injected faults (`gpu001` and `gpu002`, `GPU error XID 79`).

   ```bash
   ./scripts/benchctl get faults
   ```

6. Open the dashboards: Grafana at http://127.0.0.1:3000 (user `admin`, password `admin`) and Prometheus at http://127.0.0.1:9090.

7. Put everything back.

   ```bash
   ./scripts/benchctl clear faults
   ```

## You should see

- The GPUs of both GPU nodes show XID 79 in the DCGM metrics (the nodes have no `nvidia-smi`), and the BMC logs a critical event.
- The node health check drains each node with a reason starting `NHC:`, and a ticket is filed (in the mock, for example `OPS-17 gpu001: GPU error`, label `node-gpu001`).
- At the end the node health check resumes the nodes it drained; allow up to a minute after `scenario passed` for `describe` to show plain `IDLE`.

## Other drills

- `fan-failure`: fans fail, the node runs hot, the BMC log goes critical.
- `ib-port-down`: an InfiniBand port goes down and another flaps.

## Next

- [Alerts and dashboards](23-alerts-and-dashboards.md)
- [Write a health check](16-write-a-health-check.md)
