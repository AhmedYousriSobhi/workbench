# 16. Write a health check

**You will:** add a node health check that drains a node when a GPU runs hot, then change and remove it. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).

## Steps

1. List the checks in run order (five built in: `gpu-count`, `gpu-xid`, `ib-ports`, `nic-flap`, `real-gpu-health`).

   ```bash
   ./scripts/benchctl get checks
   ```

2. Add one from a template. It prints `added check gpu-hot. Running nodes use it at their next health check (within 30 s)`. This writes `images/node-base/nhc.d/checks.conf` and `gpu-hot.sh` in the repository: step 7 removes them again, so do not commit in between.

   ```bash
   ./scripts/benchctl add check gpu-hot --template metric-above --set metric=DCGM_FI_DEV_GPU_TEMP --set max=85
   ```

3. Validate everything. Nothing changes. It prints `ok: 6 checks valid`.

   ```bash
   ./scripts/benchctl validate checks
   ```

4. Raise the limit.

   ```bash
   ./scripts/benchctl set check gpu-hot --set max=90
   ```

5. Run it first, or switch it off and on.

   ```bash
   ./scripts/benchctl set check gpu-hot --position 1
   ./scripts/benchctl set check gpu-hot off
   ./scripts/benchctl set check gpu-hot on
   ```

6. Heat a GPU. `scenario run fan-failure` only heats the BMC's temperature and does not trip this check, so set the GPU temperature in the exporter instead. The node drains within about 30 seconds with a reason such as `NHC: DCGM_FI_DEV_GPU_TEMP 95.0 above 90`.

   ```bash
   curl -s -X POST 127.0.0.1:9400/sim/inject -d '{"node":"gpu001","temp":95}'
   ./scripts/benchctl describe node gpu001
   ```

   Then cool it. The health check resumes the node by itself.

   ```bash
   curl -s -X POST 127.0.0.1:9400/sim/inject -d '{"node":"gpu001","clear":true}'
   ```

7. Remove the check.

   ```bash
   ./scripts/benchctl remove check gpu-hot
   ```

## You should see

- Every change is validated on a copy and written only if it is valid.
- A node a check drains shows the reason `NHC: ...` in `describe node`.

## Next

- Your own script as a check, and the page's Health checks sheet: [NHC checks](../guides/nhc-checks.md).
