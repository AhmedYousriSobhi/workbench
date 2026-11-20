# 24. Write a stateful health check

**You will:** write a health check that remembers what it saw, so a GPU that sits idle and hot is drained only after 60 seconds, not on the first reading. About 15 minutes.

## You need

- A running cluster with a GPU node ([tutorial 1](01-first-cluster.md)). The examples use `gpu001`.
- The basics of checks ([tutorial 16](16-write-a-health-check.md)).

## What the check does

A node is idle when Slurm says `idle`, the GPUs show no load (at most 3 %, the simulated idle noise) and a GPU is warmer than `idle_temp`.

| Slurm state | Rule |
|---|---|
| `idle` | stay OK until the condition holds for `confirm_s` seconds, then fail |
| `allocated` or `mixed` | fail at once when a GPU is above `ceiling` (a faulty GPU) |
| anything else | forget what was seen, except after a fault: stay failed while the GPU is still above `idle_temp` |

The check keeps its memory in the file `$state`, one per check and node.
It holds nothing (OK), `SUSPECT <time>` (seen, waiting) or `FAULT <time>` (drained).

## Steps

1. Save the check. A real run uses `confirm_s=600`. The script falls back to that if the parameter is missing.

   ```bash
   cat > /tmp/idle-hot.sh <<'SH'
   # check: an idle node whose GPU stays hot (confirm_s), or a busy GPU over its ceiling
   # params: idle_temp confirm_s ceiling
   slurm=$(sinfo -h -n "$node" -o %T 2>/dev/null | head -1)
   temp=$(printf '%s\n' "$mine" | grep '^DCGM_FI_DEV_GPU_TEMP{' | awk 'BEGIN{m=0} $NF+0>m{m=$NF+0} END{print m}')
   util=$(printf '%s\n' "$mine" | grep '^DCGM_FI_DEV_GPU_UTIL{' | awk 'BEGIN{m=0} $NF+0>m{m=$NF+0} END{print m}')
   now=$(date +%s)
   case $slurm in
     allocated|mixed)
       rm -f "$state"
       [ "${temp%.*}" -gt "${P_ceiling:-87}" ] && add "GPU ${temp}C over the ceiling ${P_ceiling:-87}C while a job runs"
       ;;
     idle)
       if [ "${util%.*}" -le 3 ] && [ "${temp%.*}" -gt "$P_idle_temp" ]; then
         if [ ! -s "$state" ]; then echo "SUSPECT $now" > "$state"
         else
           since=$(awk '{print $2}' "$state")
           if [ $((now - since)) -ge "${P_confirm_s:-600}" ]; then
             echo "FAULT $since" > "$state"
             add "idle GPU at ${temp}C (above $P_idle_temp) for $((now - since)) s"
           fi
         fi
       else rm -f "$state"
       fi
       ;;
     *)
     if grep -q '^FAULT' "$state" 2>/dev/null && [ "${temp%.*}" -gt "$P_idle_temp" ]; then
       add "idle GPU still at ${temp}C (above $P_idle_temp)"
     else rm -f "$state"
     fi
     ;;
   esac
   return 0
   SH
   ```

2. Add it with short test values. It prints `added check idle-hot`.

   ```bash
   ./scripts/benchctl add check idle-hot --script /tmp/idle-hot.sh --set idle_temp=50 --set confirm_s=60 --set ceiling=87
   ```

3. Validate. It runs the check on healthy sample metrics, where it must print nothing. It prints `ok: 6 checks valid`.

   ```bash
   ./scripts/benchctl validate checks
   ```

4. Heat the idle node. This is the SUSPECT state: the node stays `idle` and the check is quiet.

   ```bash
   curl -s -X POST 127.0.0.1:9400/sim/inject -d '{"node":"gpu001","temp":60}'
   ./scripts/benchctl describe node gpu001
   ```

5. Wait about 2 minutes, then look again. The node shows `IDLE+DRAIN` with the reason `NHC: idle GPU still at 60C (above 50)`. The first run that failed said `... for 61 s`, but the next run replaces it.

   ```bash
   ./scripts/benchctl describe node gpu001
   ```

6. Cool the GPU. The node is still drained while it is hot. When it cools, the state is cleared and the node resumes within 30 seconds.

   ```bash
   curl -s -X POST 127.0.0.1:9400/sim/inject -d '{"node":"gpu001","clear":true}'
   ./scripts/benchctl describe node gpu001
   ```

7. Try the other branch. Run a GPU job, then heat the node above the ceiling. Within 30 seconds it shows `MIXED+DRAIN` with the reason `NHC: GPU 92C over the ceiling 87C while a job runs`.

   ```bash
   ./scripts/benchctl submit gpu001 --gpus 1 --duration 300
   curl -s -X POST 127.0.0.1:9400/sim/inject -d '{"node":"gpu001","temp":92}'
   ```

8. Cool it again and remove the check.

   ```bash
   curl -s -X POST 127.0.0.1:9400/sim/inject -d '{"node":"gpu001","clear":true}'
   ./scripts/benchctl remove check idle-hot
   ```

## You should see

- A short hot spell on an idle node never drains it: only `confirm_s` of it does.
- A drain by this check holds while the GPU stays hot, and lifts itself once it cools.
- With a job running, only the ceiling counts.

## Next

- The variables a check can use, and another example: [NHC checks](../guides/nhc-checks.md#worked-example-a-stateful-check).
