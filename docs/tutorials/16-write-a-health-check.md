# 16. Write a health check

**You will:** add a node health check that drains a node when a GPU runs hot, then change and remove it. About 10 minutes.

## You need

- A running cluster ([tutorial 1](01-first-cluster.md)).

## Steps

1. List the checks in run order.

   ```bash
   ./scripts/benchctl get checks
   ```

2. Add one from a template.

   ```bash
   ./scripts/benchctl add check gpu-hot --template metric-above --set metric=DCGM_FI_DEV_GPU_TEMP --set max=85
   ```

3. Validate everything. Nothing changes.

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

6. Heat a node and watch it drain within about 30 seconds.

   ```bash
   ./scripts/benchctl scenario run fan-failure
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
