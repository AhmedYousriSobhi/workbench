# ADR 0059: Governor signals: swap storm, memory.events, compressed swap

Status: accepted (issues 463 and 464)

## Context
GV-2 listed signals the governor sampled or ignored: the swap-out rate was computed and never used by the ladder, the
per-cgroup `memory.events` were never read, and `MemAvailable` ignored that zswap and zram hold their pages in RAM.

## Decision
- **Swap storm.** `pswpout` above `swap_pages_s` (default 2000 pages/s, about 8 MB/s) for `swap_sustain_s` (10 s) adds one
  level to what PSI and headroom ask for: calm becomes L2 (throttle), L2 becomes L3 (freeze and reclaim). It never goes above
  L3 and never changes L4 or L5, which stay decided by headroom and PSI full. `swap_pages_s: 0` turns it off. Both are
  `--thresholds` keys.
- **memory.events.** Every 5 s the governor reads `memory.events` of each container it governs. The totals of `high`, `oom` and
  `oom_kill` and the five containers with most `high` are in `status`; each container's counters are in `/metrics`
  (`workbench_container_memory_events`). A rise in `oom_kill` or `oom` is an audit event (`OOM KILL: NAME (+N)`). The first
  read is a baseline, so old counts raise nothing. Not read from L3 up: reading allocates (GV-8).
- **Compressed swap.** The Zswap pool (`/proc/meminfo`) and every zram device (`mem_used_total`) are taken off
  `mem_available`, which makes the ladder and admission slightly more cautious. This is a conservative choice, not a
  measured one: the kernel already counts the pools as used, so the subtraction is the margin for the pages that
  reclaim will still compress. It is shown as `compressed_swap` in `status` and `workbench_host_compressed_swap_bytes`.
  With neither enabled nothing changes.

## Consequences
- The 2000 pages/s and 10 s defaults are starting values, argued and unit-tested, not tuned on a loaded host. Tune with `--thresholds`.
- The signals file (`--signals-file`) can set `swap_out_rate` and `compressed_swap` to drive the ladder in tests.

## Issue 464: PSI trigger, pre-opened files, derived thresholds
- **PSI trigger (G3).** Unless `--no-psi-trigger` (or a test signal source) is used, the governor registers `some 150000 2000000`
  (150 ms of stall in a 2 s window) on `/proc/pressure/memory` and waits for it with `poll(POLLPRI)` instead of `sleep`. The
  trigger only **wakes the loop early**; the ladder still decides from the PSI averages, so L2/L3 sustain times and the L4
  rule (ADR 0032) are unchanged, and a short stall does not become a new level. Checked on kernel 7.0: a normal user can
  create it, but only with a window that is a multiple of 2 s (1 s is refused with EINVAL), hence 2 s. Where the kernel
  refuses, the log says why (`PSI trigger off ... (reason)`) and the loop polls every `--interval` as before.
  **Not demonstrated:** a measured latency gain. A live attempt (a throttled cgroup, a hog) could not separate the trigger from
  the averages, so the gain is the removed wait of up to one interval, argued, not measured.
- **Pre-opened files (G4).** `/proc/meminfo`, `/proc/pressure/memory`, `/proc/vmstat` and each zram `mm_stat` are opened once and
  read with `preadv` into buffers made once; a test fails if the signal path opens a file or globs. Python still builds small
  strings while parsing, so this is "no open, no new buffer", not zero allocation. zram devices are looked up again every 60 samples.
  The 5 s `memory.events` read still uses `read_text` (skipped from L3 up).
- **Derived thresholds (G5).** `host.reserve_gb` and `host.safety_floor_gb` of `sim.yaml` (read from `--workdir`, or `--sim FILE`)
  give `l4_mem` = floor, `l1_mem` = floor + (reserve - floor) / 2, calm above floor + 3/4 of the span. The shipped 3 GB and 1 GB
  give the old 1.0, 2.0 and 2.5 GB exactly. `--thresholds` still overrides each value, and the admission floor is the same
  `safety_floor_gb`. Host RAM itself is not used: the reserve already is the owner's statement of what the machine keeps.
  The governor reads the two numbers by hand because the system Python of the service may lack PyYAML.
