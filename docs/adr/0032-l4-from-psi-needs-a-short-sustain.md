# ADR 0032: L4 from a PSI stall needs a short sustain time

Status: accepted (issue 286)

## Context
L2 and L3 need their PSI condition to hold for `sustain_s` (10 s). L4 did not: one sample of PSI full above 5% went from L0
to L4 and ran `docker stop` on P6. Starting the real Jira (a JVM loading plugins) stalls memory for a moment with about 5 GB
still free; the governor stopped `jira-real` and its database twice in a row, healthy containers that were only starting.

## Decision
- L4 from PSI full > `l4_full` needs the condition to hold for `l4_sustain_s` (default 5 s, configurable with `--thresholds`).
  PSI full is already a 10 s average, so 5 s more means about 15 s of real stall; a start-up burst ends sooner.
- L4 from `MemAvailable < l4_mem` stays immediate: that is the host about to run out, not a stall that may pass.
- L5 still counts `l5_after_s` from the first moment the L4 condition (either one) is true, so the L5 timing does not move.

## Consequences
- A stall of a few seconds no longer stops containers; L2/L3 (throttle, freeze) still act at once or after 10 s as before.
- A real sustained stall reaches L4 5 s later than before. Low free memory is not delayed.
- `tests/real/test_pressure_real.py` passes `l4_sustain_s` in its fast thresholds and still drives the ladder to L4 live.
